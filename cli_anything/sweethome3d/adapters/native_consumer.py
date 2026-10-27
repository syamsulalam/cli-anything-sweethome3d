"""Native Consumer Adapter Driver.

Coordinates execution of Sweet Home 3D's native Java HomeFileRecorder via
SweetHomeConsumer to independently ingest, manipulate, and export authentic .sh3d files.
"""

import os
import sys
import json
import shutil
import subprocess
import tempfile
from typing import Optional, Dict, Any

from cli_anything.sweethome3d.utils.sweethome3d_backend import find_sweethome3d


def find_java_runtime() -> Optional[str]:
    """Find a usable Java executable (bundled javaw/java or system Java)."""
    # 1. Bundled javaw in Sweet Home 3D installation
    sh3d_exe = find_sweethome3d()
    if sh3d_exe:
        install_dir = os.path.dirname(sh3d_exe)
        for cand in [
            os.path.join(install_dir, "runtime", "bin", "javaw.exe"),
            os.path.join(install_dir, "runtime", "bin", "java.exe"),
            os.path.join(install_dir, "runtime", "bin", "java"),
        ]:
            if os.path.exists(cand):
                return cand

    # 2. System PATH
    for cmd in ["java", "java.exe", "javaw", "javaw.exe"]:
        p = shutil.which(cmd)
        if p:
            return p

    # 3. Antigravity extension JREs (common on development workstation)
    antigravity_ext = os.path.expanduser(r"~\.antigravity\extensions")
    if os.path.exists(antigravity_ext):
        for root, dirs, files in os.walk(antigravity_ext):
            if "java.exe" in files:
                return os.path.join(root, "java.exe")

    # 4. Standard JAVA_HOME
    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        for cand in [
            os.path.join(java_home, "bin", "java.exe"),
            os.path.join(java_home, "bin", "java"),
        ]:
            if os.path.exists(cand):
                return cand

    return None


def find_javac() -> Optional[str]:
    """Find javac compiler if dynamic compilation is needed."""
    for cmd in ["javac", "javac.exe"]:
        p = shutil.which(cmd)
        if p:
            return p

    antigravity_ext = os.path.expanduser(r"~\.antigravity\extensions")
    if os.path.exists(antigravity_ext):
        for root, dirs, files in os.walk(antigravity_ext):
            if "javac.exe" in files:
                return os.path.join(root, "javac.exe")

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        for cand in [
            os.path.join(java_home, "bin", "javac.exe"),
            os.path.join(java_home, "bin", "javac"),
        ]:
            if os.path.exists(cand):
                return cand

    return None


def ensure_consumer_compiled() -> bool:
    """Ensure SweetHomeConsumer.class exists, compiling if missing."""
    adapter_dir = os.path.abspath(os.path.dirname(__file__))
    class_file = os.path.join(adapter_dir, "SweetHomeConsumer.class")
    if os.path.exists(class_file):
        return True

    java_src = os.path.join(adapter_dir, "SweetHomeConsumer.java")
    if not os.path.exists(java_src):
        return False

    javac = find_javac()
    lib_dir = get_sweethome3d_lib_dir()
    if not javac or not lib_dir:
        return False

    classpath = os.path.join(lib_dir, "*")
    cmd = [javac, "--release", "8", "-cp", classpath, java_src]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return res.returncode == 0


def get_sweethome3d_lib_dir() -> Optional[str]:
    """Return the path to Sweet Home 3D lib directory."""
    sh3d_exe = find_sweethome3d()
    if sh3d_exe:
        lib_dir = os.path.join(os.path.dirname(sh3d_exe), "lib")
        if os.path.exists(lib_dir):
            return lib_dir
    return None


def run_consumer(command: str, *args: str) -> bool:
    """Execute SweetHomeConsumer with given arguments."""
    ensure_consumer_compiled()
    java_exe = find_java_runtime()
    if not java_exe:
        raise RuntimeError("No Java runtime resolved for native consumer execution.")

    lib_dir = get_sweethome3d_lib_dir()
    if not lib_dir:
        raise RuntimeError("Sweet Home 3D lib directory not found.")

    adapter_dir = os.path.abspath(os.path.dirname(__file__))
    classpath_sep = ";" if sys.platform == "win32" else ":"
    classpath = f"{adapter_dir}{classpath_sep}{os.path.join(lib_dir, '*')}"

    cmd = [java_exe, "-cp", classpath, "SweetHomeConsumer", command] + list(args)

    res = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"SweetHomeConsumer {command} failed with exit code {res.returncode}:\n"
            f"STDOUT: {res.stdout}\nSTDERR: {res.stderr}"
        )
    return True


def get_native_snapshot(sh3d_path: str) -> Dict[str, Any]:
    """Execute SweetHomeConsumer to extract an authoritative native semantic snapshot."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_json = tmp.name

    try:
        run_consumer("snapshot", os.path.abspath(sh3d_path), os.path.abspath(tmp_json))
        with open(tmp_json, "r", encoding="utf-8") as f:
            return json.load(f)
    finally:
        if os.path.exists(tmp_json):
            os.remove(tmp_json)


def native_roundtrip_sh3d(input_sh3d: str, output_sh3d: str) -> bool:
    """Execute SweetHomeConsumer roundtrip read -> write via Java HomeFileRecorder."""
    return run_consumer(
        "roundtrip",
        os.path.abspath(input_sh3d),
        os.path.abspath(output_sh3d),
    )


def verify_native_consumption(input_sh3d: str, output_sh3d: str) -> Dict[str, Any]:
    """Execute SweetHomeConsumer verify (read -> write -> re-read) and return snapshot."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_json = tmp.name

    try:
        run_consumer(
            "verify",
            os.path.abspath(input_sh3d),
            os.path.abspath(output_sh3d),
            os.path.abspath(tmp_json),
        )
        with open(tmp_json, "r", encoding="utf-8") as f:
            return json.load(f)
    finally:
        if os.path.exists(tmp_json):
            os.remove(tmp_json)
