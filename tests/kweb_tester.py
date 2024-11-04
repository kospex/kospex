#!/usr/bin/env python

import requests
import sys

def test_endpoints(base_url, endpoints):
    for endpoint in endpoints:
        url = f"{base_url}{endpoint}"
        try:
            response = requests.get(url)
            status_code = response.status_code
            status_message = response.reason

            if status_code == 200:
                print(f"✅ {url}: {status_code} {status_message}")
            elif status_code == 404:
                print(f"❌ {url}: {status_code} Not Found")
            elif status_code == 500:
                print(f"❌ {url}: {status_code} Internal Server Error")
            else:
                print(f"⚠️  {url}: {status_code} {status_message}")
        except requests.RequestException as e:
            print(f"❌ {url}: Error - {str(e)}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python script.py <base_url>")
        sys.exit(1)

    base_url = sys.argv[1]

    # List of endpoints to test
    endpoints = [
        "/",
        "/summary/",
        "/repos/",
        "/developers/",
        "/orgs/"
    ]
    print("Testing now ..\n\n")
    test_endpoints(base_url, endpoints)
