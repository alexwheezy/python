from __future__ import with_statement

import sys
import os
import re
import shutil
import requests
import platform

# Configuration-related constants
SIDEFX_OFFICIAL_URL = 'https://www.sidefx.com'
SIDEFX_SIGNUP_URL = 'https://www.sidefx.com/login/'
BUILD_DEVEL_URL = '/download/daily-builds/#category-devel'


def download_daily_build(login, password):
    ''' Downloads the file from the given url and places it in
    specified destination folder.'''

    with requests.session() as client:
        # sets cookie
        client.get(SIDEFX_SIGNUP_URL)

        # Retrieve the CSRF token first
        csrftoken = client.cookies['csrftoken']

        login_data = dict(username=login,
                          password=password,
                          csrfmiddlewaretoken=csrftoken,
                          next=BUILD_DEVEL_URL)

        # Authorized on a site for access to builds
        request = client.post(SIDEFX_SIGNUP_URL,
                              data=login_data,
                              headers=dict(Referer=SIDEFX_SIGNUP_URL),
                              timeout=None)

        # Find all references to assemblies
        build_pattern = r'<a href=[\'"]?([^\'" >]\w+.\w+.\w+.\d+.)">([\w+\_.-]+)'
        get_builds = re.findall(build_pattern, request.text)

        if not get_builds:
            print "Builds not found. \
                  Please check your username and password for authorization."
            return

        # Sort the list by the most recent build
        unix_builds = sorted(filter(lambda build:
                             get_platform() in build[-1], get_builds),
                             key=lambda num: num[0])

        unix_builds = filter(lambda build: re.match(r'\w+-\d+', build[-1]), unix_builds)
        link_build, file_build = unix_builds[-1]

        # Check updates new build
        current_build_version = check_updates()
        if current_build_version:
            build_version = re.search(r'\d{2}.\d.\d{3}', file_build).group(0)
            if build_version <= current_build_version:
                print 'The current build %s of the latest.' % build_version
                return

        build_latest_link = '%s%s%s' % (SIDEFX_OFFICIAL_URL, link_build, 'get')

        dst_dirname = os.path.dirname(os.path.realpath(__file__))
        dst_file = '%s/%s' % (dst_dirname, file_build)

        with open(dst_file, 'wb') as f:
            # Sending a request to get the build
            get_save_link = client.get(build_latest_link, stream=True)
            if get_save_link.status_code != 200:
                return

            file_size = int(get_save_link.headers.get('content-length'))
            print "Downloading: %s Bytes: %s" % (file_size, file_build)

            if file_size is None:
                f.write(get_save_link.content)
            else:
                count = 0
                for data in get_save_link.iter_content(chunk_size=8192):
                    count += len(data)
                    f.write(data)
                    done = int(50 * count / file_size)
                    sys.stdout.write("\r[%s%s] %s%c" % ('=' * done, ' ' * (50-done), done * 2, 37))
                    sys.stdout.flush()
        f.close()


def check_updates():
    ''' Checking for the latest updates.'''
    try:
        current_build_version = os.environ['HOUDINI_VERSION']
    except KeyError:
        current_build_version = None
    return current_build_version


def get_platform():
    system = platform.system().lower()
    if system == 'windows' or system == 'win32' or system == 'win64':
        return 'win'
    elif system == 'linux' or system == 'linux2':
        return 'linux'
    elif system == 'darwin':
        return 'macosx'


if __name__ == "__main__":
    import optparse

    # Parse command-line arguments.
    usage = 'usage: %prog login password'
    parser = optparse.OptionParser(usage=usage)

    options, args = parser.parse_args()

    if len(args) < 2:
        parser.error('Both login and password must be specified.')

    client_login = str(args[0])
    client_password = str(args[1])

    download_daily_build(client_login, client_password)