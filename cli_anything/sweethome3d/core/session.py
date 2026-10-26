import os
import sys
import json
import copy
import time
import shutil
from contextlib import contextmanager

from cli_anything.sweethome3d.core.project import HomeModel

SESSION_FILE = os.path.expanduser("~/.cli_anything_sweethome3d_session.json")

class ConcurrencyConflictError(RuntimeError):
    """Raised when an active session file was modified concurrently by another process."""
    pass

class RecoveryRequiredError(RuntimeError):
    """Raised when an unresolved recovery journal is present, blocking writes to prevent state corruption."""
    pass

def _calc_sha256(path):
    if not path or not os.path.exists(path):
        return None
    try:
        import hashlib
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None

def _reconcile_pending(filepath):
    """Reconciles incomplete two-phase commits if a process crashed during persistence.
    
    Must be called under exclusive session lock.
    """
    pending_file = filepath + ".pending"
    if not os.path.exists(pending_file):
        return
    try:
        with open(pending_file, "r", encoding="utf-8") as pf:
            rec = json.load(pf)
    except Exception:
        # Corrupt journal: preserve evidence for inspection, do NOT delete pending file
        return

    target = rec.get("target")
    tmp_target = rec.get("tmp_target")
    backup = rec.get("backup")
    had_target = rec.get("had_target", False)
    staged_session = rec.get("staged_session")
    expected_rev = rec.get("expected_revision", 0)
    target_rev = rec.get("target_revision")
    cand_art_sha = rec.get("candidate_artifact_sha256")
    base_art_sha = rec.get("base_artifact_sha256")

    curr_rev = 0
    if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
        try:
            with open(filepath, "r", encoding="utf-8") as sf:
                curr_rev = json.load(sf).get("revision", 0)
        except Exception:
            curr_rev = 0

    reconciled = False

    # Scenario 1: Session on disk already reached target_revision (Phase 2 completed)
    if curr_rev == target_rev:
        if backup and os.path.exists(backup):
            try:
                os.remove(backup)
            except OSError:
                pass
        if tmp_target and os.path.exists(tmp_target):
            try:
                os.remove(tmp_target)
            except OSError:
                pass
        if staged_session and os.path.exists(staged_session):
            try:
                os.remove(staged_session)
            except OSError:
                pass
        reconciled = True

    # Scenario 2: Candidate session is staged on disk
    elif staged_session and os.path.exists(staged_session):
        target_sha = _calc_sha256(target) if (target and os.path.exists(target)) else None
        tmp_target_sha = _calc_sha256(tmp_target) if (tmp_target and os.path.exists(tmp_target)) else None

        artifact_ready = False
        if cand_art_sha and target_sha == cand_art_sha:
            # Target was already published before crash
            artifact_ready = True
        elif tmp_target and os.path.exists(tmp_target) and (cand_art_sha is None or tmp_target_sha == cand_art_sha):
            # Target was NOT replaced yet, but staged candidate artifact exists: complete artifact replacement!
            try:
                os.replace(tmp_target, target)
                artifact_ready = True
            except Exception:
                artifact_ready = False

        if artifact_ready:
            # Complete session publication to match candidate artifact!
            try:
                os.replace(staged_session, filepath)
                if backup and os.path.exists(backup):
                    try:
                        os.remove(backup)
                    except OSError:
                        pass
                reconciled = True
            except Exception:
                reconciled = False
        else:
            # Candidate artifact could not be completed. We MUST roll back both session and artifact to base!
            try:
                if os.path.exists(staged_session):
                    os.remove(staged_session)
                if tmp_target and os.path.exists(tmp_target):
                    os.remove(tmp_target)
                if had_target and backup and os.path.exists(backup):
                    os.replace(backup, target)
                elif not had_target and target and os.path.exists(target):
                    os.remove(target)
                if backup and os.path.exists(backup):
                    try:
                        os.remove(backup)
                    except OSError:
                        pass
                reconciled = True
            except Exception:
                reconciled = False

    # Scenario 3: Staged session does not exist (roll back to base)
    else:
        try:
            if had_target and backup and os.path.exists(backup):
                os.replace(backup, target)
            elif not had_target and target and os.path.exists(target):
                os.remove(target)
            if backup and os.path.exists(backup):
                try:
                    os.remove(backup)
                except OSError:
                    pass
            if tmp_target and os.path.exists(tmp_target):
                try:
                    os.remove(tmp_target)
                except OSError:
                    pass
            reconciled = True
        except Exception:
            reconciled = False

    if reconciled:
        try:
            os.remove(pending_file)
        except OSError:
            pass

def _locked_acquire_and_reconcile(filepath):
    """If a pending journal exists, attempts to acquire exclusive lock.
    If lock is held by an active writer, does not interfere with the live transaction.
    If lock is acquired (process had crashed), reconciles the crash under lock.
    """
    pending_file = filepath + ".pending"
    if not os.path.exists(pending_file):
        return
    lock_file = filepath + ".lock"
    lock_fd = None
    try:
        try:
            lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            # Lock is currently held by an active writer. Do NOT touch live journal.
            if lock_fd is not None:
                try:
                    os.close(lock_fd)
                except OSError:
                    pass
                lock_fd = None
            return

        # Lock acquired: the journal was left by a terminated/crashed process.
        _reconcile_pending(filepath)
    finally:
        if lock_fd is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    try:
                        os.lseek(lock_fd, 0, os.SEEK_SET)
                        msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    import fcntl
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                os.close(lock_fd)
            except OSError:
                pass

def _locked_save_json(filepath, data, expected_revision=None, timeout_sec=5.0, post_save_action=None, artifact_commit=None):
    """Saves JSON data with OS-level exclusive file locking, two-phase artifact commit, and atomic replace."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    lock_file = filepath + ".lock"
    pending_file = filepath + ".pending"

    start_time = time.time()
    lock_fd = None
    try:
        # Acquire exclusive lock on lock_file with retry timeout
        while True:
            try:
                lock_fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
                if sys.platform == "win32":
                    import msvcrt
                    msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break  # Lock acquired
            except (OSError, IOError):
                if lock_fd is not None:
                    try:
                        os.close(lock_fd)
                    except OSError:
                        pass
                    lock_fd = None
                if time.time() - start_time > timeout_sec:
                    raise TimeoutError(f"Timed out after {timeout_sec}s acquiring session lock on '{lock_file}'.")
                time.sleep(0.02)

        # Reconcile pending commit from any previous crash
        _reconcile_pending(filepath)

        # Fail-closed check: if pending journal still exists (unresolved / corrupt), FAIL CLOSED!
        if os.path.exists(pending_file):
            raise RecoveryRequiredError(
                f"Cannot save session to '{filepath}': an unresolved recovery journal '{pending_file}' is present. "
                f"New writes are blocked to preserve evidence."
            )

        # Optimistic concurrency check under exclusive write lock
        disk_rev = 0
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            try:
                with open(filepath, "r", encoding="utf-8") as f_curr:
                    curr_d = json.load(f_curr)
                    disk_rev = curr_d.get("revision", 0)
            except Exception as ex:
                raise RuntimeError(f"Corrupt session file on disk at '{filepath}': {ex}") from ex

        if expected_revision is not None and disk_rev != expected_revision:
            raise ConcurrencyConflictError(
                f"Session revision conflict on '{filepath}': disk revision is {disk_rev}, expected {expected_revision}."
            )

        new_rev = disk_rev + 1
        data["revision"] = new_rev
        payload = json.dumps(data, indent=2)

        tx_id = f"{os.getpid()}_{time.time_ns()}"
        tmp_file = f"{filepath}.tmp_{tx_id}"

        # Write session data to temporary file
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())

        if artifact_commit:
            art_target = artifact_commit["target"]
            art_tmp = artifact_commit["tmp_target"]
            art_bak = art_target + f".bak_{tx_id}"
            had_target = os.path.exists(art_target)

            # Step 1: Prepare backup if target exists
            if had_target:
                shutil.copy2(art_target, art_bak)

            cand_art_sha = _calc_sha256(art_tmp)
            base_art_sha = _calc_sha256(art_target) if had_target else None

            # Step 2: Write pending intent record atomically (FAIL CLOSED)
            pending_record = {
                "tx_id": tx_id,
                "target": art_target,
                "tmp_target": art_tmp,
                "candidate_artifact_sha256": cand_art_sha,
                "base_artifact_sha256": base_art_sha,
                "backup": art_bak if had_target else None,
                "had_target": had_target,
                "staged_session": tmp_file,
                "expected_revision": disk_rev,
                "target_revision": new_rev,
                "active_file": data.get("active_file")
            }
            tmp_pending = pending_file + f".tmp_{tx_id}"
            try:
                with open(tmp_pending, "w", encoding="utf-8") as pf:
                    json.dump(pending_record, pf, indent=2)
                    pf.flush()
                    os.fsync(pf.fileno())
                os.replace(tmp_pending, pending_file)
            except Exception as e_pend:
                # Journal creation failed: cleanup backup and abort immediately
                if had_target and os.path.exists(art_bak):
                    try:
                        os.remove(art_bak)
                    except OSError:
                        pass
                if os.path.exists(tmp_pending):
                    try:
                        os.remove(tmp_pending)
                    except OSError:
                        pass
                if os.path.exists(tmp_file):
                    try:
                        os.remove(tmp_file)
                    except OSError:
                        pass
                raise RuntimeError(f"Failed to write commit journal '{pending_file}': {e_pend}") from e_pend

            # Step 3: Phase 1 - publish artifact
            try:
                os.replace(art_tmp, art_target)
            except Exception as e_art:
                # Phase 1 failed: session.json was untouched. Clean up backup & pending.
                if had_target and os.path.exists(art_bak):
                    try:
                        os.remove(art_bak)
                    except OSError:
                        pass
                if os.path.exists(pending_file):
                    try:
                        os.remove(pending_file)
                    except OSError:
                        pass
                if os.path.exists(tmp_file):
                    try:
                        os.remove(tmp_file)
                    except OSError:
                        pass
                raise e_art

            # Step 4: Phase 2 - publish session
            try:
                os.replace(tmp_file, filepath)
            except Exception as e_sess:
                # Phase 2 failed: rollback artifact
                rollback_ok = False
                try:
                    if had_target and os.path.exists(art_bak):
                        os.replace(art_bak, art_target)
                        rollback_ok = True
                    elif not had_target and os.path.exists(art_target):
                        os.remove(art_target)
                        rollback_ok = True
                except Exception:
                    rollback_ok = False

                if rollback_ok:
                    # Clean up journal only because rollback succeeded cleanly
                    if os.path.exists(art_bak):
                        try:
                            os.remove(art_bak)
                        except OSError:
                            pass
                    if os.path.exists(pending_file):
                        try:
                            os.remove(pending_file)
                        except OSError:
                            pass
                else:
                    # Rollback failed: PRESERVE journal and backup for crash recovery!
                    sys.stderr.write(f"CRITICAL: Artifact rollback failed during failed session commit. Journal preserved.\n")
                raise e_sess

            # Step 5: Both succeeded! Clean up backup and pending journal.
            if had_target and os.path.exists(art_bak):
                try:
                    os.remove(art_bak)
                except OSError:
                    pass
            if os.path.exists(pending_file):
                try:
                    os.remove(pending_file)
                except OSError:
                    pass
        else:
            # Atomic replacement of session
            os.replace(tmp_file, filepath)
            if post_save_action is not None:
                post_save_action()

        return new_rev
    finally:
        if lock_fd is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    try:
                        os.lseek(lock_fd, 0, os.SEEK_SET)
                        msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    import fcntl
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    except OSError:
                        pass
                os.close(lock_fd)
            except OSError:
                pass

class SessionManager:
    """Manages active session, file persistence, and undo/redo stacks."""

    def __init__(self, session_path=SESSION_FILE):
        self.session_path = session_path
        self.active_file = None
        self.current_level_id = "level1"
        self.model = HomeModel("Default Home")
        self.undo_stack = []
        self.redo_stack = []
        self.max_history = 30
        self.revision = 0
        self._pending_push = False
        self._tx_snapshot = None
        self.load_session()

    def _create_snapshot(self):
        return {
            "model": copy.deepcopy(self.model.to_dict()),
            "active_file": self.active_file,
            "current_level_id": self.current_level_id,
            "undo_stack": copy.deepcopy(self.undo_stack),
            "redo_stack": copy.deepcopy(self.redo_stack),
            "revision": self.revision,
            "pending_push": self._pending_push,
        }

    def _restore_snapshot(self, snapshot):
        self.model = HomeModel.from_dict(snapshot["model"])
        self.active_file = snapshot["active_file"]
        self.current_level_id = snapshot["current_level_id"]
        self.undo_stack = copy.deepcopy(snapshot["undo_stack"])
        self.redo_stack = copy.deepcopy(snapshot["redo_stack"])
        self.revision = snapshot["revision"]
        self._pending_push = snapshot.get("pending_push", False)

    @contextmanager
    def transaction(self):
        """Encloses snapshot creation, mutation, serialization, and rollback into a single unit.
        
        Guarantees that if any domain or persistence exception occurs, memory and history
        are restored completely without leaking pending flags or altering stacks.
        """
        snapshot = self._create_snapshot()
        self._tx_snapshot = snapshot
        try:
            yield
        except Exception:
            self._restore_snapshot(snapshot)
            self._tx_snapshot = None
            self._pending_push = False
            raise

    def set_current_level(self, level_id):
        with self.transaction():
            if not self.model.get_level(level_id):
                raise ValueError(f"Level '{level_id}' does not exist in model.")
            self.current_level_id = level_id
            self.save_session()
            return level_id

    def push_state(self):
        """Saves current state snapshot in memory before a mutation."""
        if getattr(self, "_tx_snapshot", None) is None:
            self._tx_snapshot = self._create_snapshot()
        snapshot = {
            "model": copy.deepcopy(self.model.to_dict()),
            "active_file": self.active_file,
            "current_level_id": self.current_level_id,
        }
        self.undo_stack.append(snapshot)
        if len(self.undo_stack) > self.max_history:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self._pending_push = True

    def undo(self):
        """Restores previous state from undo stack."""
        if not self.undo_stack:
            return False, "Undo stack is empty."
        with self.transaction():
            current_snapshot = {
                "model": copy.deepcopy(self.model.to_dict()),
                "active_file": self.active_file,
                "current_level_id": self.current_level_id,
            }
            self.redo_stack.append(current_snapshot)
            prev_snapshot = self.undo_stack.pop()
            if isinstance(prev_snapshot, dict) and "model" in prev_snapshot:
                self.model = HomeModel.from_dict(prev_snapshot["model"])
                self.active_file = prev_snapshot.get("active_file")
                self.current_level_id = prev_snapshot.get("current_level_id", self.current_level_id)
            else:
                self.model = HomeModel.from_dict(prev_snapshot)
            self.save_session()
            return True, "Undo successful."

    def redo(self):
        """Re-applies next state from redo stack."""
        if not self.redo_stack:
            return False, "Redo stack is empty."
        with self.transaction():
            current_snapshot = {
                "model": copy.deepcopy(self.model.to_dict()),
                "active_file": self.active_file,
                "current_level_id": self.current_level_id,
            }
            self.undo_stack.append(current_snapshot)
            next_snapshot = self.redo_stack.pop()
            if isinstance(next_snapshot, dict) and "model" in next_snapshot:
                self.model = HomeModel.from_dict(next_snapshot["model"])
                self.active_file = next_snapshot.get("active_file")
                self.current_level_id = next_snapshot.get("current_level_id", self.current_level_id)
            else:
                self.model = HomeModel.from_dict(next_snapshot)
            self.save_session()
            return True, "Redo successful."

    def new_project(self, name="New Home", wall_height=280.0):
        with self.transaction():
            candidate = HomeModel(name=name, wall_height=float(wall_height))
            self.push_state()
            self.model = candidate
            self.active_file = None
            self.current_level_id = self.model.levels[0].id if self.model.levels else "level1"
            self.save_session()
            return self.model

    def open_file(self, filepath):
        with self.transaction():
            filepath = os.path.abspath(filepath)
            if filepath.endswith(".sh3d"):
                candidate = HomeModel.load_sh3d(filepath)
            elif filepath.endswith(".json"):
                with open(filepath, "r", encoding="utf-8") as f:
                    candidate = HomeModel.from_dict(json.load(f))
            else:
                raise ValueError("Unsupported project format. Must be .sh3d or .json.")
            
            self.model = candidate
            self.active_file = filepath
            if self.model.levels and not self.model.get_level(self.current_level_id):
                self.current_level_id = self.model.levels[0].id
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.save_session()
            return self.model

    def save_file(self, filepath=None, strict_concurrency=True):
        target = filepath or self.active_file
        if not target:
            target = "untitled_home.sh3d"
        target = os.path.abspath(target)
        if not (target.endswith(".sh3d") or target.endswith(".json")):
            target += ".sh3d"

        # Stage artifact to temporary file first without modifying destination
        tmp_target = target + f".tmp_{os.getpid()}_{time.time_ns()}"
        try:
            if target.endswith(".sh3d"):
                self.model.save_sh3d(tmp_target)
            elif target.endswith(".json"):
                with open(tmp_target, "w", encoding="utf-8") as f:
                    json.dump(self.model.to_dict(), f, indent=2)

            prev_active = self.active_file
            self.active_file = target

            try:
                self.save_session(
                    strict_concurrency=strict_concurrency,
                    artifact_commit={
                        "target": target,
                        "tmp_target": tmp_target,
                    }
                )
            except Exception:
                self.active_file = prev_active
                raise

            return target
        finally:
            if os.path.exists(tmp_target):
                try:
                    os.remove(tmp_target)
                except OSError:
                    pass

    def rollback_last_push(self):
        """Rolls back the most recent uncommitted push_state() snapshot."""
        if self.undo_stack:
            prev_snapshot = self.undo_stack.pop()
            if isinstance(prev_snapshot, dict) and "model" in prev_snapshot:
                self.model = HomeModel.from_dict(prev_snapshot["model"])
                self.active_file = prev_snapshot.get("active_file")
                self.current_level_id = prev_snapshot.get("current_level_id", self.current_level_id)
            else:
                self.model = HomeModel.from_dict(prev_snapshot)

    def save_session(self, strict_concurrency=True, post_save_action=None, artifact_commit=None):
        try:
            data = {
                "active_file": self.active_file,
                "current_level_id": self.current_level_id,
                "revision": self.revision,
                "model": self.model.to_dict(),
                "undo_stack": self.undo_stack[-self.max_history:],
                "redo_stack": self.redo_stack[-self.max_history:],
                "undo_count": len(self.undo_stack),
                "redo_count": len(self.redo_stack),
            }
            exp_rev = self.revision if strict_concurrency else None
            new_rev = _locked_save_json(
                self.session_path, data, expected_revision=exp_rev, post_save_action=post_save_action, artifact_commit=artifact_commit
            )
            self.revision = new_rev
            self._pending_push = False
            self._tx_snapshot = None
            return True
        except Exception as e:
            if getattr(self, "_tx_snapshot", None) is not None:
                self._restore_snapshot(self._tx_snapshot)
                self._tx_snapshot = None
                self._pending_push = False
            elif getattr(self, "_pending_push", False):
                self.rollback_last_push()
                self._pending_push = False
            if isinstance(e, (ConcurrencyConflictError, RecoveryRequiredError)):
                raise
            sys.stderr.write(f"Error: Failed to persist session state to '{self.session_path}': {e}\n")
            raise RuntimeError(f"Session persistence failed: {e}") from e

    def load_session(self):
        pending_file = self.session_path + ".pending"
        if os.path.exists(pending_file):
            _locked_acquire_and_reconcile(self.session_path)
        if os.path.exists(self.session_path) and os.path.getsize(self.session_path) > 0:
            try:
                with open(self.session_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.active_file = data.get("active_file")
                    self.current_level_id = data.get("current_level_id", "level1")
                    self.revision = data.get("revision", 0)
                    if "model" in data:
                        self.model = HomeModel.from_dict(data["model"])
                    self.undo_stack = data.get("undo_stack", [])
                    self.redo_stack = data.get("redo_stack", [])
            except Exception as e:
                raise RuntimeError(f"Corrupt session file at '{self.session_path}': {e}") from e
