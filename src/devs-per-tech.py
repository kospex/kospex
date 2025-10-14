import csv
from collections import defaultdict

def count_developers_by_language(csv_file):
    """
    Count unique developers per language from a CSV file.

    Args:
        csv_file: Path to the CSV file

    Returns:
        Dictionary with language as key and count of unique developers as value
    """
    # Dictionary to store sets of unique emails per language
    language_developers = defaultdict(set)

    # Read the CSV file
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)

        for row in reader:
            email = row['author_email'].strip()
            language = row['language'].strip()

            # Add the email to the set for this language
            language_developers[language].add(email)

    # Convert sets to counts
    developer_counts = {lang: len(emails) for lang, emails in language_developers.items()}

    return developer_counts

def main():
    import sys

    # Check if CSV file path was provided
    if len(sys.argv) < 2:
        print("Usage: python count_developers.py <csv_file> [-csv]")
        print("Example: python count_developers.py developers.csv")
        print("Example: python count_developers.py developers.csv -csv")
        sys.exit(1)

    csv_file = sys.argv[1]
    output_csv = '-csv' in sys.argv

    try:
        counts = count_developers_by_language(csv_file)

        if output_csv:
            # CSV output
            print("language,# Devs")
            for language in sorted(counts.keys()):
                print(f"{language},{counts[language]}")
        else:
            # Human-readable output
            print("Unique Developers per Language:")
            print("-" * 40)
            for language in sorted(counts.keys()):
                print(f"{language:20} : {counts[language]:3} developers")

            print("-" * 40)
            print(f"Total languages: {len(counts)}")

    except FileNotFoundError:
        print(f"Error: File '{csv_file}' not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
