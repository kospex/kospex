""" A module to query the dep.dev API and parse package manager files"""
import datetime
import os
import sys
import dateutil.parser
import requests
from prettytable import PrettyTable

packages = {}

def deps_dev(package_type,package_name,version):
    """ Query the Deps.dev API for a package and version"""
    #url = f"https://api.github.com/repos/{package_name}/releases/latest"
    base_url = "https://api.deps.dev/v3alpha/systems"
    url = f"{base_url}/{package_type}/packages/{package_name}/versions/{version}"
    #/v3alpha/systems/{versionKey.system}/packages/{versionKey.name}/versions/{versionKey.version}
    #https://api.deps.dev/v3alpha/systems/pypi/packages/requests/versions/2.31.0
    # links -> which has a SOURCE_REPO label should be the git
    req = requests.get(url)
    if req.status_code == 200:
        return req.json()
    else:
        return None

def get_pypi_package_info(package):
    """ Get the latest version of a package from PyPI """
    url = f"https://pypi.org/pypi/{package}/json"
    req = requests.get(url)
    if req.status_code == 200:
        return req.json()["info"]["version"]
    else:
        return None

def get_latest_version(package):
    """ Get the latest version of a package from PyPI """
    url = f"https://pypi.org/pypi/{package}/json"
    req = requests.get(url)
    if req.status_code == 200:
        return req.json()["info"]["version"]
    else:
        return None


def assess(filename):
    """ Using deps.dev to assess and provide a summary of the package manager file """

    basefile = os.path.basename(filename)
    if basefile != "requirements.txt":
        print(f"Only requirements.txt files are supported.  Found {basefile}")
        sys.exit(1)

    today = datetime.datetime.now(datetime.timezone.utc)
    table = PrettyTable()
    table.field_names = ["package", "version", "days_ago", 
                     "published_at", "source_repo", "advisories", "default"]    
    table.align["package"] = "l"
    table.align["version"] = "r"
    table.align["days_ago"] = "r"
    table.align["published_at"] = "l"
    table.align["source_repo"] = "l"
    table.align["advisories"] = "r"
    table.align["default"] = "r"

    # TODO - write functions to parse based on file type
    # (e.g. requirements.txt, pom.xml, packge.json, etc.)
    with open(filename, 'r', encoding="utf-8") as pmf:
        for line in pmf.readlines():
            entry = []
            if line.startswith('#'):
                continue
            package = line.split('==')[0]
            version = line.split('==')[1].strip()
            entry.append(package)
            entry.append(version)
            packages[package] = version
            info = deps_dev("pypi",package,version)
            print(f"{package} : {version}")
            if info is None:
                print(f"Could not find {package} in deps.dev")
                continue
            print(info.get("publishedAt"))
            pub_date = dateutil.parser.isoparse(info.get("publishedAt"))
            diff = today - pub_date
            print(f"days ago: {diff.days}")
            entry.append(diff.days)
            entry.append(info.get("publishedAt"))
            source_repo = ""
            if info.get("links") is not None:
                for link in info.get("links"):
                    if link.get("label") == "SOURCE_REPO":
                        print(link.get("url"))
                        source_repo = link.get("url")
            entry.append(source_repo)
            advisories = info.get("advisoryKeys")
            if advisories:
                entry.append(len(advisories))
            else:
                entry.append(0)

            entry.append(info.get("isDefault"))

            print()
            table.add_row(entry)

        print(table)

def find_dependency_files(directory):
    """ Find all dependency files (packagae managers) in a directory and its subdirectories."""
    # Map of filename to its package manager
    package_files = {
        'requirements.txt': 'PyPi',
        'Pipfile': 'PyPi (Pipenv)',
        'Pipfile.lock': 'PyPi (Pipenv)',
        'setup.py': 'PyPi',
        'pyproject.toml': 'PyPi (Poetry/Flit/etc.)',
        'Gemfile': 'RubyGems',
        'Gemfile.lock': 'RubyGems',
        'package.json': 'npm',
        'yarn.lock': 'Yarn',
        'composer.json': 'Composer',
        'composer.lock': 'Composer',
        'pom.xml': 'Maven',
        'build.gradle': 'Gradle',
        'build.gradle.kts': 'Gradle',
        'Cargo.toml': 'Cargo (Rust)',
        'Cargo.lock': 'Cargo (Rust)',
        'go.mod': 'Go Modules',
        'go.sum': 'Go Modules',
        'Podfile': 'CocoaPods',
        'Podfile.lock': 'CocoaPods',
    }

    detected_files = []

    # Use os.walk() to recursively search through directory and its subdirectories
    for root, dirs, files in os.walk(directory):
        for filename in files:
            if filename in package_files:
                # Append the full path to the file, and its type
                detected_files.append((os.path.join(root, filename), package_files[filename]))

    return detected_files


#today = datetime.datetime.now(datetime.timezone.utc)
