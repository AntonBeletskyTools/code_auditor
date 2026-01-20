# code_auditor
Automated code audit and optimization.

=====================================================================
          GEMINI CODE QUALITY AUDITOR (v2.0) - README FILE
=====================================================================

1. DESCRIPTION
--------------
This script is an automated Code Quality Pipeline designed for 
modern web development. It acts as a "Virtual Senior Developer" 
by combining two powerful auditing methods:

A) AI ANALYSIS: Uses Google Gemini 1.5 Flash to detect logic bugs, 
   security vulnerabilities (XSS, SQLi), and Clean Code violations.
B) W3C VALIDATION: Connects to official W3C servers to verify 
   HTML and CSS syntax compliance.

The final output is a professional, interactive HTML report 
containing all found issues and suggestions for fixing them.


2. SYSTEM REQUIREMENTS
----------------------
* Python 3.9 or higher.
* An active Internet connection (for AI and W3C APIs).
* A Google Gemini API Key (get it at: https://aistudio.google.com/).


3. INSTALLATION
---------------
Open your terminal or command prompt and install the 
required libraries:

   pip install google-genai requests python-dotenv


4. CONFIGURATION
----------------
Before running the script, you must provide your API Key.
You have two ways to do this:

A) Environment Variable: Create a file named ".env" in the 
   script folder and add:
   GEMINI_API_KEY=your_key_here

B) Direct Edit: Open "code_auditor.py" and replace the placeholder
   value in the API_KEY variable (line 30).


5. HOW TO USE
-------------
1. Place the "code_auditor.py" script in your project's root 
   directory.
2. Ensure your source code is in a folder named "src" 
   (you can change this folder name in the CONFIG section 
   inside the script).
3. Run the script:

   python code_auditor.py

4. Wait for the process to finish. It respects API rate limits 
   (15 requests per minute) so it may take some time for 
   large projects.


6. OUTPUT
---------
Once finished, the script will generate a file:
"audit_report.html"

Open this file in any web browser to see the detailed 
results of the audit.


7. FILE TYPES SUPPORTED
-----------------------
The script scans for: .html, .css, .js, .jsx, .ts, .tsx, .scss


=====================================================================
       (c) 2024-2026 - POWERED BY GEMINI AI - QUALITY FIRST
=====================================================================
