from pathlib import Path

from setuptools import find_packages, setup

here = Path(__file__).parent
version_ns: dict[str, str] = {}
exec((here / "fichaxebot" / "__init__.py").read_text(encoding="utf-8"), version_ns)

with (here / "requirements.txt").open(encoding="utf-8") as req_file:
    requirements = [
        line.strip()
        for line in req_file
        if line.strip() and not line.startswith("#")
    ]

setup(
    name="fichaxebot",
    version=version_ns["__version__"],
    description="Telegram bot to automate USC fichaxe check-ins",
    author="",
    packages=find_packages(include=["fichaxebot", "fichaxebot.*"]),
    install_requires=requirements,
    package_data={"fichaxebot": ["resources/*.html"]},
    include_package_data=True,
    python_requires=">=3.10",
    entry_points={
        "console_scripts": ["fichaxebot=fichaxebot.bot:main"],
    },
)
