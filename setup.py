from setuptools import setup

APP = ["sleepless.py"]
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "LSUIElement": True,
        "CFBundleName": "Sleepless",
        "CFBundleIdentifier": "com.alancho.sleepless",
        "CFBundleShortVersionString": "1.0.0",
    },
    "packages": ["rumps", "psutil"],
    "resources": ["assets/icons"],
}

setup(
    app=APP,
    name="Sleepless",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
