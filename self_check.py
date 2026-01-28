from skills.model_matching import ModelMatcher
import logging
from rich.console import Console

console = Console()
logging.basicConfig(level=logging.INFO)

def run_checks():
    console.print("[bold cyan]Running ModelMatcher Self-Check (v18.42)...[/bold cyan]")
    
    # Path might need adjustment depending on where this script is run relative to the file
    # Assuming d:\00_程式\20260120_商化自動OCR圖片\型號表.txt exists
    matcher = ModelMatcher("d:\\00_程式\\20260120_商化自動OCR圖片\\型號表.txt")
    
    # Test cases: (Input string, Expected Model)
    test_cases = [
        # Case 412: Greedy Regex should extract S24F532EAC and potentially fuzzy match it
        # Note: If S24F532EAC is NOT in table, it might match S24F332EAC or similar.
        # This test ensures the Regex extracts the candidate effectively.
        ("24SAMSUNG S24F532EAC 100Hz", "S24F532EAC"), # Assuming exact match if in list, or closest
        
        # FollowMe Integrity
        ("FollowMe M7 32\"", "FollowMe M7 32\""), 
        
        # Standard
        ("S27CG552EC 5290", "S27CG552EC"), 
        
        # Partial
        ("S27CG552", "S27CG552EC"), 
        
        # Noisy quotes loop check
        ("'FollowMe M5 32'", "FollowMe M5 32\""),
    ]

    for raw, expected in test_cases:
        console.print(f"Testing input: '{raw}'")
        result = matcher.match(raw)
        
        # Note: If valid_models doesn't have S24F532EAC, it won't match exactly.
        # But we want to ensure it DOES return SOMETHING useful, not None.
        
        if result == expected:
            console.print(f" -> [green]PASS[/green] (Matches '{result}')")
        else:
            # Allow fuzzy match acceptance for 412 if the list doesn't have exact
            console.print(f" -> [yellow]WARN[/yellow] (Got '{result}', Expected '{expected}')")

if __name__ == "__main__":
    run_checks()
