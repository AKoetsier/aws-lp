"""setuptools installation script"""
import io
from os import path

from setuptools import setup, find_packages

from aws_lp import __author__, __version__

# get the long description from the readme
with io.open(path.join(path.abspath(path.dirname(__file__)), 'README.md'),
             mode='rb') as readme:
    LONG_DESCRIPTION = readme.read()

setup(
    name='aws-lp',
    version=__version__,
    description='Tool for using AWS CLI with LastPass SAML',
    long_description=LONG_DESCRIPTION,
    long_description_content_type='text/markdown',
    author=__author__,
    author_email='bleblan2@unb.ca',
    url='https://github.com/omnibrian/aws-lp',
    license='GPLv3',
    keywords='lastpass aws awscli boto3',
    packages=find_packages(exclude=['contrib', 'docs', 'tests']),
    install_requires=[
        'awscli',
        'boto3',
        'click',
        'requests',
        'six',
    ],
    entry_points={
        'console_scripts': [
            'aws-lp=aws_lp.main:main',
        ]
    }
)
