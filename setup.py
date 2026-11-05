"""Compatibility build entry for Python installations with older setuptools."""
from setuptools import setup,find_namespace_packages
setup(name='workcontext-services',version='0.3.0',description='Agent context, capability execution, local source archives and controlled skill sync',python_requires='>=3.9',packages=find_namespace_packages(include=['workcontext*']),include_package_data=False,package_data={'workcontext':['web/*.html','web/*.css','web/*.js','web/*.mjs','migrations/p2/*.sql']},entry_points={'console_scripts':['workcontext=workcontext.__main__:main']})
