from setuptools import setup

setup(
    name="thing",
    install_requires=["six>=1.16", "jinja2"],
    extras_require={"dev": ["tox"]},
)
