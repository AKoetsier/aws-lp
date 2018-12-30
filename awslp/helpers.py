"""Shared helpers"""
import base64
import binascii
import hashlib
import hmac
import logging
import re
import struct
from xml.etree import ElementTree

import boto3
from six.moves import input
from six.moves import html_parser

from awslp.exceptions import MfaRequiredException


LASTPASS_SERVER = 'https://lastpass.com'
LOGGER = logging.getLogger(__name__)


def should_verify(lastpass_server):
    """ Disable SSL validation only when debugging via proxy """
    return LASTPASS_SERVER in lastpass_server


def extract_form(html):
    """Retrieve the (first) form elements from an html page."""
    fields = {}
    matches = re.findall(r'name="([^"]*)" (id="([^"]*)" )?value="([^"]*)"',
                         html)

    for match in matches:
        if len(match) > 2:
            fields[match[0]] = match[3]

    action = ''
    match = re.search(r'action="([^"]*)"', html)

    if match:
        action = match.group(1)

    return {
        'action': action,
        'fields': fields
    }


def xorbytes(string_a, string_b):
    """XOR all bytes in a string"""
    return ''.join(map(lambda x, y: chr(ord(x) ^ ord(y)), string_a, string_b))


def prf(hsh, data):
    """Internal hash update for pbkdf2/hmac-sha256"""
    hshm = hsh.copy()
    hshm.update(data)
    return hshm.digest()


def pbkdf2(password, salt, rounds, length):
    """PBKDF2-SHA256 password derivation."""
    key = ''
    hsh = hmac.new(password, None, hashlib.sha256)

    for block in range(0, (length + 31) / 32):
        index = hval = prf(hsh, salt + struct.pack('>I', block + 1))

        for _ in range(1, rounds):
            hval = prf(hsh, hval)
            index = xorbytes(index, hval)

        key = key + index

    return binascii.hexlify(key[0:length])


def lastpass_login_hash(username, password, iterations):
    """Determine the number of PBKDF2 iterations needed for a user."""
    key = binascii.unhexlify(pbkdf2(password, username, iterations, 32))
    result = pbkdf2(key, password, 1, 32)
    return result


def lastpass_iterations(session, lastpass_server, username):
    """Determine the number of PBKDF2 iterations needed for a user."""
    iterations = 5000
    lp_iterations_page = f'{lastpass_server}/iterations.php'

    params = {
        'email': username
    }

    response = session.post(lp_iterations_page, data=params,
                            verify=should_verify(lastpass_server))

    if response.status_code == 200:
        iterations = int(response.text)

    return iterations


def lastpass_login(session, lastpass_server, username, password, otp=None):
    """Log into LastPass with a given username and password."""
    LOGGER.debug('logging into lastpass as %s', username)
    iterations = lastpass_iterations(session, lastpass_server, username)

    lp_login_page = f'{lastpass_server}/login.php'

    params = {
        'method': 'web',
        'xml': '1',
        'username': username,
        'hash': lastpass_login_hash(username, password, iterations),
        'iterations': iterations
    }

    if otp is not None:
        params['otp'] = otp

    response = session.post(lp_login_page, data=params,
                            verify=should_verify(lastpass_server))
    response.raise_for_status()

    doc = ElementTree.fromstring(response.text)
    error = doc.find('error')

    if error is not None:
        cause = error.get('cause')
        if cause == 'googleauthrequired':
            raise MfaRequiredException('Need MFA for this login')
        else:
            reason = error.get('message')
            raise ValueError(f'Could not login to lastpass: {reason}')


def get_saml_token(session, lastpass_server, saml_cfg_id):
    """
    Log into LastPass and retrieve a SAML token for a given
    SAML configuration.
    """
    LOGGER.debug('Getting SAML token')

    # now logged in, grab the SAML token from the IdP-initiated login
    idp_login = f'{lastpass_server}/saml/launch/cfg/{saml_cfg_id}'

    response = session.get(idp_login, verify=should_verify(lastpass_server))

    form = extract_form(response.text)

    if not form['action']:
        # try to scrape the error message just to make it more user friendly
        error = ''

        for line in response.text.splitlines():
            match = re.search(r'<h2>(.*)</h2>', line)
            if match:
                msg = html_parser.HTMLParser().unescape(match.group(1))
                msg = msg.replace('<br/>', '\n')
                msg = msg.replace('<b>', '')
                msg = msg.replace('</b>', '')
                error = '\n' + msg

        raise ValueError('Unable to find SAML ACS' + error)

    return base64.b64decode(form['fields']['SAMLResponse'])


def get_saml_aws_roles(assertion):
    """Get the AWS roles contained in the assertion.

    This returns a list of RoleARN, PrincipalARN (IdP) pairs.
    """
    doc = ElementTree.fromstring(assertion)

    role_attrib = 'https://aws.amazon.com/SAML/Attributes/Role'
    xpath = f".//saml:Attribute[@Name='{role_attrib}']/saml:AttributeValue"

    namespace = {
        'saml': 'urn:oasis:names:tc:SAML:2.0:assertion'
    }

    attribs = doc.findall(xpath, namespace)
    return [a.text.split(',', 2) for a in attribs]


def get_saml_nameid(assertion):
    """Get the nameid contained in the assertion.

    This returns a list of nameids.
    """
    doc = ElementTree.fromstring(assertion)

    namespace = {
        'saml': 'urn:oasis:names:tc:SAML:2.0:assertion'
    }

    return doc.find('.//saml:NameID', namespace).text


def prompt_for_role(roles):
    """Ask user which role to assume."""
    if len(roles) == 1:
        return roles[0]

    print('Please select a role:')
    count = 1

    for role in roles:
        print(f'  {count}) {role[0]}')
        count = count + 1

    choice = 0

    while choice < 1 or choice > len(roles) + 1:
        try:
            choice = int(input('Choice: '))
        except ValueError:
            choice = 0

    return roles[choice - 1]


def aws_assume_role(assertion, role_arn, principal_arn):
    """Assume role with SAML using boto3.

    returns {
        'Credentials': {
            'AccessKeyId': '',
            'SecretAccessKey': '',
            'SessionToken': ''
        }
    }
    """
    client = boto3.client('sts')

    return client.assume_role_with_saml(
                RoleArn=role_arn,
                PrincipalArn=principal_arn,
                SAMLAssertion=base64.b64encode(assertion))