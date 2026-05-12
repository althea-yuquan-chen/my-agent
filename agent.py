import ollama

# Store conversation history for memory
history = []

def chat(user_message):
    # Add user message to history
    history.append({
        "role": "user", 
        "content": user_message
    })
    
    # Send to Qwen3
    response = ollama.chat(
        model="qwen3:14b",
        messages=history
    )
    
    # Extract reply
    reply = response["message"]["content"]
    
    # Add reply to history so it remembers context
    history.append({
        "role": "assistant",
        "content": reply
    })
    
    return reply

def main():
    print("🤖 Local Agent Ready (type 'quit' to exit)\n")
    
    while True:
        user_input = input("You: ").strip()
        
        if user_input.lower() == "quit":
            print("Bye!")
            break
            
        if not user_input:
            continue
            
        print("\nAgent: thinking...", end="\r")
        reply = chat(user_input)
        print(f"Agent: {reply}\n")

if __name__ == "__main__":
    main()