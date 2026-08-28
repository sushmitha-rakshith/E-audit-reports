import pandas as pd

# Excel file paths
file1 = "/home/fastpace_rpa/Documents/E audit reports/downloads/combined_report_20260820_135454.xlsx"
file2 = "/home/fastpace_rpa/Documents/E audit reports/downloads/combined_report_20260820_140321.xlsx"

# Read both Excel files
df1 = pd.read_excel(file1)
df2 = pd.read_excel(file2)

# Combine rows
combined = pd.concat([df1, df2], ignore_index=True)

# Save the combined file
combined.to_excel("combined.xlsx", index=False)

print("Excel files combined successfully!")