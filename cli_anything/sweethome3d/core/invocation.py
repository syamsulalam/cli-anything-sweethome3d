"""Invocation and authority resolution for Sweet Home 3D CLI."""
from __future__ import annotations
from cli_anything.sweethome3d.core.exceptions import UnsupportedFeatureError, ValidationRejectedError

import hashlib
import os


def resolve_invocation(project=None, session=None, *, default_session):
    """Resolve authoritative session path and identity for an invocation.

    Enforces that at most one of --project or --session is specified,
    canonicalizes paths, verifies existing directory for --project,
    and rejects hardlinked state paths to prevent lock aliasing.
    """
    if project is not None and session is not None:
        raise ValidationRejectedError("Choose --project OR --session, not both")
    if project is not None:
        project_root = os.path.normcase(os.path.realpath(os.path.abspath(project)))
        if not os.path.isdir(project_root):
            raise ValidationRejectedError("--project must name an existing workspace directory")
        path = os.path.join(project_root, ".cli-anything", "session.json")
        mode = "project"
    else:
        project_root = None
        path = session if session is not None else default_session
        mode = "session" if session is not None else "default"
    path = os.path.normcase(os.path.realpath(os.path.abspath(os.fspath(path))))
    # Hardlinked state paths would create multiple lock names for one object.
    if os.path.isfile(path) and os.stat(path).st_nlink > 1:
        raise UnsupportedFeatureError("Hardlinked session authority is unsupported")
    return {
        "session_path": path,
        "authority_id": "path-sha256:" + hashlib.sha256(os.fsencode(path)).hexdigest(),
        "project_root": project_root,
        "selection": mode,
    }
