#!/usr/bin/env python3
"""
Parse krunner-osi.csv to analyze repository compliance.

A repository is considered COMPLIANT if ALL its packages meet at least one of:
  - versions_behind <= 2 (n-2 or newer)
  - published_at is less than 3 months old
"""

import csv
import sys
import argparse
from datetime import datetime, timedelta
from collections import defaultdict


def parse_date(date_str):
    """Parse date string in format YYYY-MM-DD."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def is_package_compliant(versions_behind, published_at, cutoff_date):
    """
    Check if a package is compliant.

    Args:
        versions_behind: Number of versions behind (should be <= 2)
        published_at: Publication date string
        cutoff_date: Date 3 months ago from now

    Returns:
        tuple: (is_compliant: bool, has_missing_data: bool)
    """
    has_missing_data = False

    # Check if versions_behind <= 2
    versions_valid = False
    try:
        if int(versions_behind) <= 2:
            return True, False  # Compliant, no missing data
        versions_valid = True
    except (ValueError, TypeError):
        has_missing_data = True

    # Check if published_at is less than 3 months old
    pub_date = parse_date(published_at)
    if pub_date and pub_date >= cutoff_date:
        return True, False  # Compliant, no missing data
    elif not pub_date:
        has_missing_data = True

    # If we have valid data but non-compliant
    if versions_valid or pub_date:
        return False, False

    # Both fields are missing/invalid
    return False, has_missing_data


def analyze_csv(filename, results_file=None):
    """Analyze the CSV file and calculate compliance statistics."""

    # Calculate the cutoff date (3 months ago from today)
    today = datetime.now()
    cutoff_date = today - timedelta(days=90)

    print(f"Analysis Date: {today.strftime('%Y-%m-%d')}")
    print(f"3-Month Cutoff: {cutoff_date.strftime('%Y-%m-%d')}")
    print("="*70)
    print()

    # Dictionary to store packages per repository
    repo_packages = defaultdict(list)
    # Dictionary to store unique dependency files per repository
    repo_files = defaultdict(set)

    # Read CSV file
    try:
        with open(filename, 'r') as f:
            reader = csv.DictReader(f)

            for row in reader:
                repo_id = row.get('_repo_id', 'Unknown')
                package_name = row.get('package_name', 'Unknown')
                versions_behind = row.get('versions_behind', '')
                published_at = row.get('published_at', '')
                file_path = row.get('file_path', '')

                is_compliant, has_missing = is_package_compliant(versions_behind, published_at, cutoff_date)

                repo_packages[repo_id].append({
                    'package': package_name,
                    'versions_behind': versions_behind,
                    'published_at': published_at,
                    'compliant': is_compliant,
                    'missing_data': has_missing
                })

                # Track unique dependency files
                if file_path:
                    repo_files[repo_id].add(file_path)

    except FileNotFoundError:
        print(f"Error: File '{filename}' not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file: {e}")
        sys.exit(1)

    # Analyze repositories
    compliant_repos = []
    non_compliant_repos = []
    repo_stats = []  # For CSV output

    for repo_id, packages in repo_packages.items():
        total_packages = len(packages)
        compliant_packages = sum(1 for pkg in packages if pkg['compliant'])
        non_compliant_packages = total_packages - compliant_packages
        missing_data_packages = sum(1 for pkg in packages if pkg['missing_data'])

        pct_compliant = (compliant_packages / total_packages * 100) if total_packages > 0 else 0
        pct_non_compliant = (non_compliant_packages / total_packages * 100) if total_packages > 0 else 0

        # Get unique dependency file count
        unique_files = len(repo_files[repo_id])

        # Store stats for CSV output
        repo_stats.append({
            'repo_id': repo_id,
            '% Compliant': f"{pct_compliant:.1f}",
            '% Non Compliant': f"{pct_non_compliant:.1f}",
            'Total dependencies': total_packages,
            'Unique dependency files': unique_files,
            'Missing data': missing_data_packages
        })

        # A repository is compliant if ALL packages are compliant
        all_compliant = all(pkg['compliant'] for pkg in packages)

        if all_compliant:
            compliant_repos.append(repo_id)
        else:
            non_compliant_repos.append((repo_id, packages))

    # Calculate percentages
    total_repos = len(repo_packages)
    compliant_count = len(compliant_repos)
    non_compliant_count = len(non_compliant_repos)

    compliant_pct = (compliant_count / total_repos * 100) if total_repos > 0 else 0
    non_compliant_pct = (non_compliant_count / total_repos * 100) if total_repos > 0 else 0

    # Print summary
    print("COMPLIANCE SUMMARY")
    print("="*70)
    print(f"Total Repositories: {total_repos}")
    print()
    print(f"✓ Compliant Repositories:     {compliant_count:3d} ({compliant_pct:5.1f}%)")
    print(f"✗ Non-Compliant Repositories: {non_compliant_count:3d} ({non_compliant_pct:5.1f}%)")
    print("="*70)
    print()

    # Print details of non-compliant repositories
    if non_compliant_repos:
        print("NON-COMPLIANT REPOSITORIES (with issues)")
        print("="*70)

        for repo_id, packages in non_compliant_repos:
            non_compliant_packages = [pkg for pkg in packages if not pkg['compliant']]
            missing_data_packages = [pkg for pkg in packages if pkg['missing_data']]

            print(f"\n{repo_id}")
            print(f"  Total packages: {len(packages)}, Non-compliant: {len(non_compliant_packages)}")

            if missing_data_packages:
                print(f"  ⚠ Packages with missing data: {len(missing_data_packages)}")

            for pkg in non_compliant_packages[:5]:  # Show first 5 non-compliant packages
                missing_indicator = " ⚠ MISSING DATA" if pkg['missing_data'] else ""
                print(f"    • {pkg['package']}{missing_indicator}")
                print(f"      Versions behind: {pkg['versions_behind'] or 'N/A'}, Published: {pkg['published_at'] or 'N/A'}")

            if len(non_compliant_packages) > 5:
                print(f"    ... and {len(non_compliant_packages) - 5} more non-compliant packages")

        print()

    # Print compliant repositories
    if compliant_repos:
        print("COMPLIANT REPOSITORIES")
        print("="*70)
        for repo_id in compliant_repos:
            package_count = len(repo_packages[repo_id])
            print(f"✓ {repo_id} ({package_count} packages)")
        print()

    print("="*70)
    print("CRITERIA:")
    print("  A repository is COMPLIANT if ALL packages meet at least one of:")
    print("    • versions_behind <= 2 (n-2 or newer)")
    print("    • published_at is less than 3 months old")
    print("="*70)

    # Write results to CSV if requested
    if results_file:
        try:
            with open(results_file, 'w', newline='') as f:
                fieldnames = ['repo_id', '% Compliant', '% Non Compliant', 'Total dependencies', 'Unique dependency files', 'Missing data']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(repo_stats)

            print()
            print(f"✓ Results written to: {results_file}")
        except Exception as e:
            print()
            print(f"✗ Error writing results file: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Analyze krunner-osi.csv for repository compliance',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python parse_krunner_osi.py
  python parse_krunner_osi.py krunner-osi.csv
  python parse_krunner_osi.py -results output.csv
  python parse_krunner_osi.py krunner-osi.csv -results results.csv
        """
    )

    parser.add_argument(
        'filename',
        nargs='?',
        default='krunner-osi.csv',
        help='CSV file to analyze (default: krunner-osi.csv)'
    )

    parser.add_argument(
        '-results',
        metavar='FILE.csv',
        help='Output results to CSV file with per-repo compliance statistics'
    )

    args = parser.parse_args()

    analyze_csv(args.filename, results_file=args.results)
