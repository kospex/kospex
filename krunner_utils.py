""" Helper functions for krunner """

def extract_grep_parameters(input_string):
    """Extracts a grep command string into filename, line_number and string_contents. """
    # Split the input string at colons
    parts = input_string.split(":")
    if len(parts) < 3:
        return None
    # Extract the first two parameters and the rest of the string
    #first_param = parts[0].lstrip("./") if len(parts) > 0 else ""
    first_param = parts[0].lstrip(".").lstrip("/") if len(parts) > 0 else ""
    # the strip should remove the ./ in filenames
    second_param = parts[1] if len(parts) > 1 else ""
    rest_of_string = ":".join(parts[2:]) if len(parts) > 2 else ""

    # Return the extracted parameters and the rest of the string as an array
    return [first_param, second_param, rest_of_string]
