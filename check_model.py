
try:
    with open('型號表.txt', 'r', encoding='utf-8') as f:
        content = f.read()
    
    target = "S27CG552EC"
    print(f"File Size: {len(content)}")
    print(f"Target '{target}' in file: {target in content}")
    print(f"Case-insensitive match: {target.upper() in content.upper()}")
    
    if target in content:
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if target in line:
                print(f"Found at line {i+1}: {repr(line)}")

except Exception as e:
    print(f"Error: {e}")
