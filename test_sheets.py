import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

SPREADSHEET_ID = '12_YjQiqDa39YRaiJfhdzbmwCSOh33NpgsKseU3yiA4A'  # ganti dengan punya kamu

# Connect ke Google Sheets
creds = Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
client = gspread.authorize(creds)

spreadsheet = client.open_by_key(SPREADSHEET_ID)
sheet = spreadsheet.sheet1

# Append baris test
sheet.append_row([
    datetime.now().strftime('%d/%m/%Y'),  # Tanggal
    'Kopi Test',                           # Nama Item
    'Makan & Minum',                       # Kategori
    25000,                                 # Harga
    'Test dari Python'                     # Catatan
])

print("Berhasil! Cek Google Sheets kamu.")