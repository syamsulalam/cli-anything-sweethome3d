import os
import sys
import json
import copy

from cli_anything.sweethome3d.core.project import HomeModel

SESSION_FILE = os.path.expanduser("~/.cli_anything_sweethome3d_session.json")

def _locked_save_json(filepath, data):
    """Saves JSON data with OS-level exclusive file locking to prevent concurrent write corruption."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    payload = json.dumps(data, indent=2)

    # File open mode: r+ if exists, w+ if not
    mode = "r+" if os.path.exists(filepath) else "w+"
    with open(filepath, mode, encoding="utf-8") as f:
        # Acquire lock
        if sys.platform == "win32":
            import msvcrt
            # Windows lock first byte
            f.seek(0)
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                locked = True
            except OSError:
                locked = False
        else:
            import fcntl
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                locked = True
            except OSError:
                locked = False

        try:
            f.seek(0)
            f.write(payload)
            f.truncate()
            f.flush()
        finally:
            if locked:
                if sys.platform == "win32":
                    try:
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
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
        self.load_session()

    def set_current_level(self, level_id):
        if not self.model.get_level(level_id):
            raise ValueError(f"Level '{level_id}' does not exist in model.")
        self.current_level_id = level_id
        self.save_session()
        return level_id

    def push_state(self):
        """Saves current model state to undo stack before a mutation."""
        snapshot = copy.deepcopy(self.model.to_dict())
        self.undo_stack.append(snapshot)
        if len(self.undo_stack) > self.max_history:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.save_session()

    def undo(self):
        """Restores previous state from undo stack."""
        if not self.undo_stack:
            return False, "Undo stack is empty."
        current_state = copy.deepcopy(self.model.to_dict())
        self.redo_stack.append(current_state)
        prev_state = self.undo_stack.pop()
        self.model = HomeModel.from_dict(prev_state)
        self.save_session()
        return True, "Undo successful."

    def redo(self):
        """Re-applies next state from redo stack."""
        if not self.redo_stack:
            return False, "Redo stack is empty."
        current_state = copy.deepcopy(self.model.to_dict())
        self.undo_stack.append(current_state)
        next_state = self.redo_stack.pop()
        self.model = HomeModel.from_dict(next_state)
        self.save_session()
        return True, "Redo successful."

    def new_project(self, name="New Home", wall_height=280.0):
        self.push_state()
        self.model = HomeModel(name=name, wall_height=wall_height)
        self.active_file = None
        self.save_session()
        return self.model

    def open_file(self, filepath):
        filepath = os.path.abspath(filepath)
        if filepath.endswith(".sh3d"):
            self.model = HomeModel.load_sh3d(filepath)
        elif filepath.endswith(".json"):
            with open(filepath, "r", encoding="utf-8") as f:
                self.model = HomeModel.from_dict(json.load(f))
        else:
            raise ValueError("Unsupported project format. Must be .sh3d or .json.")
        
        self.active_file = filepath
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.save_session()
        return self.model

    def save_file(self, filepath=None):
        target = filepath or self.active_file
        if not target:
            target = "untitled_home.sh3d"
        target = os.path.abspath(target)

        if target.endswith(".sh3d"):
            self.model.save_sh3d(target)
        elif target.endswith(".json"):
            with open(target, "w", encoding="utf-8") as f:
                json.dump(self.model.to_dict(), f, indent=2)
        else:
            # Default to .sh3d
            target += ".sh3d"
            self.model.save_sh3d(target)

        self.active_file = target
        self.save_session()
        return target

    def save_session(self):
        data = {
            "active_file": self.active_file,
            "current_level_id": self.current_level_id,
            "model": self.model.to_dict(),
            "undo_count": len(self.undo_stack),
            "redo_count": len(self.redo_stack),
        }
        try:
            _locked_save_json(self.session_path, data)
        except Exception:
            pass

    def load_session(self):
        if os.path.exists(self.session_path):
            try:
                with open(self.session_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.active_file = data.get("active_file")
                    self.current_level_id = data.get("current_level_id", "level1")
                    if "model" in data:
                        self.model = HomeModel.from_dict(data["model"])
            except Exception:
                pass
