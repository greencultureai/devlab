#!/usr/bin/env python
# vim: set syntax=python et ts=4 sw=4 sts=4:
"""
This is a script to help in installing and setting up the devlab executable
"""

import argparse
import distutils.spawn
import json
import logging
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import tarfile
from io import BytesIO

try:
    #For python3
    from urllib import request as url_request
    from urllib.error import HTTPError
    from html.parser import HTMLParser
except ImportError:
    #For python2
    import urllib2 as url_request
    from urllib2 import HTTPError
    from HTMLParser import HTMLParser

## Variables
ARGS = None
DEF_HTTP_URL = 'https://github.com/evernym/devlab/releases'
LOGGING_LEVELS = {
    'debug': logging.DEBUG,
    'info': logging.INFO,
    'warning': logging.WARNING,
    'error': logging.ERROR,
    'critical': logging.CRITICAL,
    'notset': logging.NOTSET
}
PARSER = None

## Classes
class FileIndexParser(HTMLParser, object): #pylint: disable=abstract-method
    """
    Subclass of HTMLParser, for finding tags with an href and adding them to an
    internal 'parsed' list attribute.
    """
    def __init__(self, *args, **kwargs):
        """Initialize and reset this instance."""
        super(FileIndexParser, self).__init__(*args, **kwargs)
        self.parsed = list()
        self.reset()
    def handle_starttag(self, tag, attrs):
        """Process a starttag and look for 'a' tags"""
        if tag == 'a':
            for attr in attrs:
                if attr[0] == 'href':
                    self.parsed.append(attr[1])

## Functions
def action_install(repo_path, set_version=None, **kwargs):
    """
    The default action, which is to install/update the package

    Args:
        repo_path: str, Location where the packages are to be sourced from
        set_version: str, The version of the package to install
    """
    ignored_args = kwargs
    log = logging.getLogger('Install')
    cur_version = find_cur_version()
    elev_priv = False
    log.debug("Found version: %s", cur_version)
    if cur_version != 'unknown':
        if cur_version == set_version:
            log.info("Version: %s, already installed!", set_version)
            sys.exit(0)
        log.info("Found devlab existing installation. Version=%s", cur_version)
        if cur_version == 'master':
            log.info("Devlab version is using a master version that tracks git. Use git to pull latest changes")
            sys.exit(0)
        log.info("Checking for newer version...")
    else:
        log.info("This will be a new installation. Checking for available versions")
    packages = list_packages(repo_path, logger=log)
    versions = list(packages.keys())
    if set_version:
        if set_version not in versions:
            log.error("Cannot find version: %s", set_version)
            sys.exit(1)
    else:
        latest_version = find_latest_version(versions)
        set_version = latest_version
    if cur_version != 'unknown':
        if cur_version != set_version:
            log.info("There is a newer version available! Will upgrade to: %s", set_version)
        else:
            log.info("You already have the latest version!")
            sys.exit(0)
    log.info("Downloading version: %s....", set_version)
    status, data = http_request(packages[set_version]['path'], decode=False, logger=log)
    log.info("Downloading done. Status=%s size=%s", status, len(data))
    if status:
        homedir = os.path.expanduser('~')
        log.info("Successfully downloaded package, attempting to extract package to: %s/devlab", homedir)
        try:
            tarball = BytesIO(data)
            tar_file = tarfile.open(fileobj=tarball)
            tar_file.extractall(path=homedir)
            if os.path.isdir('{}/devlab'.format(homedir)):
                os.chmod('{}/devlab/devlab'.format(homedir), 0o755)
            log.info("Successfully extracted devlab package")
            if not os.path.exists('/usr/local/bin/devlab'):
                log.info("Creating symlink to /usr/local/bin")
                if not os.path.isdir('/usr/local/bin'):
                    elev_priv = True
                else:
                    cur_euid = os.geteuid()
                    if cur_euid != 0:
                        if os.stat('/usr/local/bin').st_uid != cur_euid:
                            elev_priv = True
                create_links(homedir=homedir, elev_priv=elev_priv, logger=log)
            else:
                log.info("Devlab executable already exists in /usr/local/bin. Skipping")
        except Exception: #pylint: disable=broad-except
            exc_type, exc_value = sys.exc_info()[:2]
            exc_str = "Failed extracting devlab package: {exc_type}: {exc_val}".format(
                exc_type=exc_type.__name__,
                exc_val=exc_value
            )
            log.error(exc_str)
    else:
        log.error("Failed downloading tarball")

def action_list(repo_path, **kwargs):
    """
    List the available versions of the devlab package

    Args:
        repo_path: str, Location where the packages are to be sourced from
    """
    ignored_args = kwargs
    log = logging.getLogger('List')
    packages = list_packages(repo_path, logger=log)
    versions = list(packages.keys())
    find_latest_version(versions)
    for ver in versions:
        print("Version: {version} Path: {path}".format(**packages[ver]))

def action_uninstall(**kwargs):
    """
    Uninstall devlab

    Args:
        None
    """
    ignored_args = kwargs
    log = logging.getLogger('Uninstall')
    cur_euid = os.geteuid()
    homedir = os.path.expanduser('~')
    elev_priv = False
    if not os.path.islink('/usr/local/bin/devlab'):
        log.info("There is no devlab executable found at: '/usr/local/bin/devlab'. Already uninstalled?")
        return
    if cur_euid != 0:
        if os.lstat('/usr/local/bin/devlab').st_uid != cur_euid:
            elev_priv = True
    delete_links(elev_priv=elev_priv, logger=log)
    if os.path.isdir('{}/devlab'.format(homedir)):
        log.info("Removing devlab code from: %s/devlab", homedir)
        shutil.rmtree('{}/devlab'.format(homedir))

def create_links(homedir, elev_priv=False, logger=None):
    """
    Create symlink and elevate priveleges if needed

    Args:
        homedir: str, of the path where devlab has been extracted
        elev_priv: bool, whether to elevate priveleges or not
        logger: Logger, logger to use for log messages
    """
    if logger:
        log = logger
    else:
        log = logging.getLogger('create_links')
    if elev_priv:
        log.debug("Elevating permissions with sudo, to create symlink")
        if not os.path.isdir('/usr/local/bin'):
            log.info("Found that /usr/local/bin didn't exist... creating...")
            subprocess.call(['/usr/bin/sudo', 'mkdir', '-p', '/usr/local/bin'])
        log.info("Creating symlink: %s -> %s", '/usr/local/bin/devlab', '{}/devlab/devlab'.format(homedir))
        subprocess.call(['/usr/bin/sudo', 'ln', '-s', '{}/devlab/devlab'.format(homedir), '/usr/local/bin/devlab'])
    else:
        log.debug("Creating symlink: %s -> %s", '/usr/local/bin/devlab', '{}/devlab/devlab'.format(homedir))
        os.symlink('{}/devlab/devlab'.format(homedir), '/usr/local/bin/devlab')

def delete_links(elev_priv=False, logger=None):
    """
    Delete the symlink and elevate priveleges if needed

    Args:
        elev_priv: bool, whether to elevate priveleges or not
        logger: Logger, logger to use for log messages
    """
    if logger:
        log = logger
    else:
        log = logging.getLogger('delete_links')
    if elev_priv:
        log.debug("Elevating permissions with sudo, to delete symlink")
        if os.path.exists('/usr/local/bin/devlab'):
            log.info("Removing link: '/usr/local/bin/devlab'")
            subprocess.call(['/usr/bin/sudo', 'rm', '/usr/local/bin/devlab'])
    else:
        log.info("Removing link: '/usr/local/bin/devlab'")
        os.remove('/usr/local/bin/devlab')

def find_latest_version(versions):
    """
    Go through the versions, sort them and find the highest number

    Args:
        versions: list, the versions to use
    Returns:
        str or None if the highest version was able to be found
    """
    def human_keys(astr):
        """
        Sorts keys based on human order.. IE 1 is less than 10 etc..

        alist.sort(key=human_keys) sorts in human order
        """
        keys = []
        for elt in re.split(r'(\d+)', astr):
            elt = elt.swapcase()
            try:
                elt = int(elt)
            except ValueError:
                pass
            keys.append(elt)
        return keys
    versions.sort(key=human_keys)
    if versions:
        return versions[-1]
    return None

def find_cur_version():
    """
    Look at devlab and find the __VERSION__ string inside

    Args:
        None

    Returns:
        str
    """
    log = logging.getLogger('find_cur_version')
    os.chdir(
        os.path.expanduser('~')
    )
    cur_path = distutils.spawn.find_executable('devlab')
    log.debug("Path where 'devlab' executable resides: %s", cur_path)
    if cur_path == 'devlab':
        cur_path = None
    cur_version = 'unknown'
    if cur_path:
        with open(cur_path) as dfile:
            line = dfile.readline()
            while line:
                if line.startswith('__VERSION__'):
                    cur_version = line.split('=')[1].strip(" '\"\n")
                    break
                line = dfile.readline()
    return cur_version

def http_request(url, headers=None, payload=None, insecure=False, decode=True, logger=None):
    """
    Make an HTTP request

    Args:
        url: str
            Url to send the request to
        headers: dict
            Optional dictionary of headers to pass
        payload: dict
            Optional payload of dict values to send
        insecure: bool
            Optional flag to indicate whether certificate validation to the
            http server should happen.
        decode: bool
            Optional flag to indicat whether the string response should have
            '.decode()' run on it. Default is True, but if binary data is
            needed for later processing then setting to False is what you're
            looking for
        logger:
            Optional logger object
    Returns Tuple
        Element 1: Bool: Indicating success
        Element 2: Str: Response from the request to the HTTP server
    """
    ctx = None
    cafile = None
    data = None
    if not headers:
        headers = dict()
    if logger:
        log = logger
    else:
        log = logging.getLogger('http_request')
    if payload:
        headers['Content-Type'] = 'application/json'
        # payload = json.dumps(payload).encode()
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = url_request.Request(
        url=url,
        data=payload,
        headers=headers
    )
    try:
        rsp = url_request.urlopen(req, timeout=5, cafile=cafile, context=ctx)
        code = rsp.code
    except HTTPError as exc_val:
        data = exc_val.read()
        code = exc_val.code
        log.error("Failed sending request to %s: HTTPError: %s", url, exc_val)
    except Exception: #pylint: disable=broad-except
        exc_type, exc_value = sys.exc_info()[:2]
        exc_str = "Failed sending request to {url}: {exc_type}: {exc_val}".format(
            url=url,
            exc_type=exc_type.__name__,
            exc_val=exc_value
        )
        log.error(exc_str)
        return (False, exc_value)
    if not data:
        data = rsp.read()
    if decode:
        data = data.decode()
    if code >= 200 and code <= 299:
        return (True, data)
    return (False, data)

def list_packages(path, logger):
    """
    Look at path. If it is an HTTP url then query it for the packages to
    generate a list of devlab packages found there. If it is a local directory
    then do a directory listing for the list of packages.

    Args:
        path: str, either local directory or HTTP url with <a href links
        logger: Logger, logging object to use for log messages
    """
    if logger:
        log = logger
    else:
        logger = logging.getLogger('list_packages')
    packages = {}
    path = path.lower()
    found_files = []
    if 'http' in path:
        log.debug("Repo path is an HTTP url")
        if 'github.com/' in path and path.endswith('releases'):
            log.debug('Repo path is a github releases url. Looking for devlab packages in releases')
            path_split = path.split('/')
            path_split.pop()
            github_repo = path_split.pop()
            github_owner = path_split.pop()
            log.debug("Parsed path results in github owner: '%s' and github repo: '%s'", github_owner, github_repo)
            path = 'https://api.github.com/repos/{}/{}/releases'.format(github_owner, github_repo)
            releases_page = 1
            while True:
                http_rsp = http_request("{}?page={}".format(path, releases_page))
                if http_rsp[0]:
                    try:
                        json_rsp = json.loads(http_rsp[1])
                    except:
                        log.error("Response body: '%s'", http_rsp[1])
                        log.error("Failed getting releases from: '%s'", path)
                        raise
                    if not json_rsp:
                        log.debug('No more releases returned. Done')
                        break
                    for release in json_rsp:
                        try:
                            assets = release['assets']
                        except KeyError:
                            assets = []
                        for asset in assets:
                            found_files.append(asset['browser_download_url'])
                    releases_page += 1
                    log.debug("Checking for more releases on page: %s", releases_page)
                    continue
                else:
                    log.error("Response body: '%s'", http_rsp[1])
                    log.error("Failed getting releases from: '%s'", path)
                break
        else:
            log.debug('Assuming repo path is an html index of files')
            http_rsp = http_request(path)
            if http_rsp[0]:
                log.debug("Successfully received response from HTTP server... Looking for devlab packages")
                files_parser = FileIndexParser()
                files_parser.feed(http_rsp[1])
                found_files = files_parser.parsed
            else:
                log.error("Request to: '%s' Failed. Response: '%s'", path, http_rsp[1])
    else:
        if not os.path.isdir(path):
            log.error("Repo path is not found: %s", path)
        else:
            log.warning("This feature has not been implemented yet")
    for file_found in found_files:
        name, ext = os.path.splitext(
            os.path.basename(file_found)
        )
        if name.startswith('devlab_') and ext.lower() in ('.tar.gz', '.tgz'):
            log.debug("Parsing metadata from found devlab package: '%s'", file_found)
            metadata = parse_pkg_name(
                os.path.basename(file_found)
            )
            if not metadata:
                log.warning("Could not parse metadata from file: '%s'. Skipping...", file_found)
                continue
            if 'http' in file_found.lower():
                metadata['path'] = file_found
            else:
                metadata['path'] = '{}/{}'.format(path, file_found)
            packages[metadata['version']] = metadata
    return packages

def logging_init(level):
    """
    Initialize and create initial LOGGER
    level is a String of one of:
        'trace'
        'debug'
        'info'
        'warning'
        'error'
        'critical'
        'notset'
    Colorizing was combining multiple ideas in the answers from:
        https://stackoverflow.com/q/384076
    """
    black, red, green, yellow, blue, magenta, cyan, white = range(8) # pylint: disable=unused-variable
    level_colors = {
        logging.WARNING  : 30 + yellow,
        logging.INFO     : 30 + green,
        logging.DEBUG    : 30 + white,
        logging.CRITICAL : 30 + yellow,
        logging.ERROR    : 40 + red
    }
    sequences = {
        'reset': "\033[0m",
        'color': "\033[1;%dm",
        'bold' : "\033[1m"
    }
    #Initialize logging
    try:
        log_level = int(level)
    except ValueError:
        log_level = LOGGING_LEVELS[level.lower()]
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    #Setup ANSI coloring for the log level name
    if platform.system() != 'Windows' and ISATTY:
        for l_level in level_colors:
            logging.addLevelName(
                l_level,
                "{bold}{color_seq}{level_name}{reset}".format(
                    color_seq=sequences['color'] % level_colors[l_level],
                    level_name=logging.getLevelName(l_level),
                    **sequences
                )
            )

def parse_pkg_name(filename, logger=None):
    """
    Parse a filename to try and determine a package name, version, arch etc...

    Args:
        filename: str, of the filename to parse
        logger: logger, logging object to use for log messages
    """
    name, ext = os.path.splitext(filename)
    version = None
    arch = None
    if logger:
        log = logger
    else:
        log = logging.getLogger('parse_pkg_name')
    if ext == '.gz':
        if name[-4:] == '.tar':
            name = name[:-4]
            ext = '.tar.gz'
    name = os.path.basename(name)
    split_name = name.split('_')
    if split_name:
        name = split_name.pop(0)
    if split_name:
        version = split_name.pop(0)
    if split_name:
        arch = '_'.join(split_name)
    if not version:
        log.error("Filename: %s has no version in it", filename)
        return None
    try:
        vcheck = int(version[0]) #pylint: disable=unused-variable
    except ValueError:
        log.error("Filename: %s is not parsable", filename)
    return {
        'name': name,
        'ext': ext[1:],
        'version': version,
        'arch': arch
    }

def set_default_action(args, subparser):
    """
    Look at the args passed and determine if there is a subparse action set for
    it. If there is, then return the normal set of args. If NOT then append the
    default 'install' action and return it.

    This is primarily to get around a shortcoming in python2 :-|

    Args:
        args: list, of the args passed to the script

    Returns:
        list
    """
    action_exists = False
    args_passed = list(args)
    for action in subparser.choices:
        if action in args_passed:
            action_exists = True
            break
    if not action_exists:
        args_passed.append('install')
    return args_passed

##- Main -##
#Check to see if we are attached to a TTY
try:
    ISATTY = sys.stdout.isatty()
except AttributeError:
    ISATTY = False

if __name__ == '__main__':
    #Top level parser
    PARSER = argparse.ArgumentParser(description='Main interface into the devlab installer')
    PARSER.add_argument('--log-level', '-l', choices=list(LOGGING_LEVELS.keys()), default='info', help='Set the log-level output. Default=info')
    PARSER.add_argument('--repo-path', '-p', default=DEF_HTTP_URL, help='Path or URL to where devlab tgz packages are stored. Default={}'.format(DEF_HTTP_URL))
    PARSER.set_defaults(func=action_install)
    SUBPARSERS = PARSER.add_subparsers(help='Actions')

    #Add Subparser for install
    PARSER_INSTALL = SUBPARSERS.add_parser('install', help='Install devlab package')
    PARSER_INSTALL.add_argument('--set-version', '-V', default=None, help='Set the specific version of devlab to install')
    PARSER_INSTALL.set_defaults(func=action_install)

    #Add Subparser for list
    PARSER_LIST = SUBPARSERS.add_parser('list', help='List available versions')
    PARSER_LIST.set_defaults(func=action_list)

    #Add Subparser for Uninstall
    PARSER_UNINSTALL = SUBPARSERS.add_parser('uninstall', help='UN-Install devlab package')
    PARSER_UNINSTALL.set_defaults(func=action_uninstall)

    #Parse our args
    ARGS = PARSER.parse_args(
        set_default_action(args=sys.argv[1:], subparser=SUBPARSERS)
    )

    #Initialize logging:
    logging_init(level=ARGS.log_level)
    LOGGER = logging.getLogger("Main")

    #Run the action function
    sys.exit(ARGS.func(**vars(ARGS)))
