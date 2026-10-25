from setuptools import setup, find_namespace_packages

with open("SWEETHOME3D.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="cli-anything-sweethome3d",
    version="1.0.0",
    author="Syamsul Alam & Gemini 3.8 Flash (Antigravity Orchestrator)",
    author_email="dev@codev.id",
    description="Agent-native CLI harness for Sweet Home 3D floor planning software",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/HKUDS/CLI-Anything",
    packages=find_namespace_packages(include=["cli_anything.*"]),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Multimedia :: Graphics :: 3D Modeling",
        "Topic :: Scientific/Engineering :: Architectural Design",
    ],
    python_requires=">=3.10",
    install_requires=[
        "click>=8.0",
    ],
    entry_points={
        "console_scripts": [
            "cli-anything-sweethome3d=cli_anything.sweethome3d.sweethome3d_cli:main",
        ],
    },
)
