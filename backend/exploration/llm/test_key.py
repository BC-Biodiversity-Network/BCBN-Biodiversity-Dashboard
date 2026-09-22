"""
Check that the API key works, before spending time on anything bigger.

This sends one tiny request and prints what comes back, including how many
tokens it used. If this works, the key is good and the package is installed
correctly. If it does not, the error message here is much easier to read than
one that appears halfway through a 900 record run.

Usage:

    python exploration/llm/test_key.py
    python exploration/llm/test_key.py --model gemini-3.5-flash-lite
"""

import argparse
import os
import sys


def main():
    """Send one small request and report whether it worked."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="gemini-3.5-flash-lite",
                        help="Model to test. Use the name shown in AI Studio.")
    args = parser.parse_args()

    # Load the .env file if python-dotenv is installed. This puts GEMINI_API_KEY
    # into the environment so the SDK can find it. Without it you would have to
    # type "export GEMINI_API_KEY=..." in the terminal every time.
    #
    # find_dotenv walks up the folder tree from this script, so the file can sit
    # in exploration/llm, in backend, or at the top of the repo. Printing which
    # one it found saves a lot of confusion when there is more than one.
    try:
        from dotenv import find_dotenv, load_dotenv
        found = find_dotenv()
        if found:
            load_dotenv(found)
            print(f"read {found}")
        else:
            print("no .env file found anywhere above this script")
    except ImportError:
        print("python-dotenv is not installed, relying on the environment instead")

    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        print()
        print("No key found.")
        print("Create a file called .env in the backend folder with one line in it:")
        print("    GEMINI_API_KEY=your-key-here")
        print()
        print("Then check git is ignoring it, using the real path:")
        print("    git check-ignore -v backend/.env")
        return 1

    # Never print the whole key. Showing the first and last few characters is
    # enough to tell whether the right one got loaded.
    print(f"key found: {key[:6]}...{key[-4:]}  ({len(key)} characters)")

    try:
        from google import genai
    except ImportError:
        print()
        print("The SDK is missing. Install it with:")
        print("    pip install google-genai")
        return 1

    client = genai.Client()

    print(f"asking {args.model} one question...")
    print()
    try:
        response = client.models.generate_content(
            model=args.model,
            contents="Name one bird that lives in British Columbia. Reply with just the name.",
        )
    except Exception as error:
        print("The call failed.")
        print(f"   {type(error).__name__}: {error}")
        print()
        print("Common causes:")
        print("  - the key is wrong or was revoked")
        print("  - this model name does not exist, check the list in AI Studio")
        print("  - the free tier quota for today is used up")
        print("  - billing is not enabled on the project and this model needs it")
        return 1

    print(f"reply: {response.text.strip()}")

    usage = getattr(response, "usage_metadata", None)
    if usage:
        inp = getattr(usage, "prompt_token_count", 0) or 0
        out = getattr(usage, "candidates_token_count", 0) or 0
        print(f"tokens: {inp} in, {out} out")
        # At 0.05 and 0.20 dollars per million tokens, one small call like this
        # costs a tiny fraction of a cent. Printing it makes the scale obvious.
        cost = inp / 1e6 * 0.05 + out / 1e6 * 0.20
        print(f"cost of this one call: about ${cost:.8f}")

    print()
    print("Working. You can run the smoke test now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
