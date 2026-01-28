from skills.model_matching import ModelMatcher
import logging
from rich.console import Console

console = Console()
logging.basicConfig(level=logging.INFO)

# Mocking the Orchestrator and Logic to simulate Batch Processor behavior
class MockOrchestrator:
    def log_system(self, msg):
        console.print(f"[LOG] {msg}")

def test_followme_bypass():
    console.print("[bold cyan]Running FollowMe Bypass Check (v18.44)...[/bold cyan]")
    
    orchestrator = MockOrchestrator()
    
    # Simulate the logic block from batch processor
    # Conditions: Identified as FollowMe, clean_model is set, list check happens
    
    test_cases = [
        {"raw": "FollowMe M7 32\"", "price": "12990", "expect_bypass": True, "expect_model": "FollowMe M7 32\""},
        {"raw": "Unknown Model", "price": None, "expect_bypass": False, "expect_model": None}, # Should fail validation
    ]

    # Load Valid Models to simulate the 'not in list' check
    valid_models_list = ["S24F332EAC", "S27CG552EC"] # FollowMe NOT in this list to test Bypass

    for case in test_cases:
        raw_model = case['raw']
        clean_model = raw_model # Simplify
        data_obj = {"model": raw_model, "price": case['price']}
        
        console.print(f"Testing: {raw_model}")
        
        # --- LOGIC REPLICATION START ---
        is_followme_bypass = False
        if "FollowMe" in clean_model or "FOLLOWME" in clean_model.upper():
             mapped_model = 'FollowMe M7 32"' # Simplified mapping
             clean_model = mapped_model
             is_followme_bypass = True
             orchestrator.log_system(f"⚠️ [FollowMe Logic] mapped to '{clean_model}' >> BYPASS CHECK")

        # The Crucial Check
        if clean_model not in valid_models_list and not is_followme_bypass:
             console.print(f" -> [red]BLOCKED[/red] by Hallucination Check")
             final_model = None
        else:
             console.print(f" -> [green]PASSED[/green] (Valid or Bypassed)")
             final_model = clean_model
        # --- LOGIC REPLICATION END ---

        if final_model == case['expect_model']:
            console.print(f" -> [bold green]TEST PASS[/bold green]")
        else:
            console.print(f" -> [bold red]TEST FAIL[/bold red] (Got {final_model}, Expected {case['expect_model']})")

if __name__ == "__main__":
    test_followme_bypass()
