import os
import sys
import shutil
import subprocess

def find_sweethome3d():
    """Locate the Sweet Home 3D executable on the system.
    
    Checks standard PATH and well-known operating system install locations.
    Returns the absolute path to the executable if found, or None.
    """
    # 1. Check PATH
    cmd = shutil.which("SweetHome3D") or shutil.which("sweethome3d") or shutil.which("SweetHome3D.exe")
    if cmd:
        return cmd

    # 2. Check Windows standard directories
    if sys.platform == "win32":
        candidates = [
            r"C:\Program Files\Sweet Home 3D\SweetHome3D.exe",
            r"C:\Program Files (x86)\Sweet Home 3D\SweetHome3D.exe",
            os.path.expanduser(r"~\AppData\Local\Programs\Sweet Home 3D\SweetHome3D.exe"),
        ]
        for c in candidates:
            if os.path.exists(c):
                return c

    # 3. Check macOS standard directories
    elif sys.platform == "darwin":
        mac_path = "/Applications/Sweet Home 3D.app/Contents/MacOS/SweetHome3D"
        if os.path.exists(mac_path):
            return mac_path

    # 4. Check Linux standard directories
    elif sys.platform.startswith("linux"):
        linux_candidates = [
            "/usr/bin/sweethome3d",
            "/usr/local/bin/sweethome3d",
            "/opt/sweethome3d/SweetHome3D",
        ]
        for c in linux_candidates:
            if os.path.exists(c):
                return c

    return None

def get_install_instructions():
    """Return platform-specific installation instructions for Sweet Home 3D."""
    if sys.platform == "win32":
        return "Windows: Run 'winget install eTeks.SweetHome3D' or download from https://www.sweethome3d.com/download.jsp"
    elif sys.platform == "darwin":
        return "macOS: Run 'brew install --cask sweet-home-3d' or download from https://www.sweethome3d.com/download.jsp"
    else:
        return "Linux: Run 'sudo apt install sweethome3d' or download from https://www.sweethome3d.com/download.jsp"

def launch_gui(sh3d_path=None):
    """Launch Sweet Home 3D GUI with an optional project file."""
    exe = find_sweethome3d()
    if not exe:
        raise RuntimeError(
            f"Sweet Home 3D executable was not found on this system.\n"
            f"Install guide: {get_install_instructions()}"
        )
    
    args = [exe]
    if sh3d_path and os.path.exists(sh3d_path):
        args.append(sh3d_path)
    
    # Non-blocking launch in background
    if sys.platform == "win32":
        subprocess.Popen(args, creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        subprocess.Popen(args, start_new_session=True)
    
    return {"status": "launched", "executable": exe, "project": sh3d_path}
