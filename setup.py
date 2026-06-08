from setuptools import setup

APP = ["sleepless.py"]
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "LSUIElement": True,
        "CFBundleName": "Sleepless",
        "CFBundleIdentifier": "ai.pressw.sleepless",
        "CFBundleShortVersionString": "1.0.0",
    },
    "packages": ["rumps", "psutil"],
}

setup(
    app=APP,
    name="Sleepless",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
