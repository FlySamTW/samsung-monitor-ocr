
try:
    from skills.official_price import get_price_manager
    print("Loading Price Manager...")
    pm = get_price_manager()
    
    target_models = ['S32CG552EC', 'S32DM703UC', 'S27FG532EC']
    
    for model in target_models:
        price = pm.get_official_price(model)
        print(f"Model: {model}, Official Price: {price}, Type: {type(price)}")
        
        # Simulate Logic
        if price and price > 0:
            print(f"  -> Check would RUN.")
            if model == 'S32CG552EC':
                ocr_price = 1000
                diff = abs(ocr_price - price)
                print(f"  -> Diff with 1000: {diff}")
                if diff > 5000:
                    print("  -> ALERT WOULD TRIGGER")
                else:
                    print("  -> Alert would NOT trigger (Diff <= 5000)")
        else:
            print(f"  -> Check would SKIP (Price invalid).")

except Exception as e:
    print(f"Error: {e}")
