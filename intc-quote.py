import yfinance as yf

def get_intel_quote():
    try:
        intel = yf.Ticker("INTC")
        data = intel.history(period="1d", interval="1m")  # Get today's minute-level data
        latest_price = data["Close"].iloc[-1]  # Last closing price
        return latest_price
    except Exception as e:
        return f"Error: {str(e)}"

# Example usage
if __name__ == "__main__":
    price = get_intel_quote()
    print(f"Current Intel (INTC) stock price: ${price:.2f}")
