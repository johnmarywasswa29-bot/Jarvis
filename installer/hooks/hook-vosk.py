from PyInstaller.utils.hooks import collect_all

# Ensure all Vosk DLLs are collected with correct destination
all_files = collect_all("vosk")

# collect_all returns (datas, binaries, hiddenimports)
# but for DLLs we need them as binaries going to 'vosk' destination
datas = []
binaries = []

# Process datas (first element)
for src, dest in all_files[0]:
    if src.endswith('.dll'):
        # DLLs need to go to 'vosk' destination so they're in the vosk package directory
        binaries.append((src, 'vosk'))
    else:
        datas.append((src, dest))

# Process binaries (second element) 
for src, dest in all_files[1]:
    # These are already marked as binaries, ensure destination is 'vosk'
    binaries.append((src, 'vosk'))

hiddenimports = all_files[2]
