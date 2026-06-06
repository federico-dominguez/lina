#!/usr/bin/env python3
"""Test: send message directly to LINA goosed and see what happens."""
import asyncio, json, time, httpx

async def test():
    url = "https://localhost:3000"
    secret = "4bea469dd75fb2de8cf3f797c832813575bc5408fab3ae3a2b6734037971e564"
    headers = {"Content-Type": "application/json", "x-secret-key": secret}
    
    session_id = "comm-bridge-test"
    
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0, read=60.0, write=30.0, pool=5.0), verify=False) as client:
        # Ensure session
        r = await client.get(f"{url}/sessions/{session_id}", headers=headers)
        print(f"GET /sessions/{session_id}: {r.status_code}")
        
        if r.status_code != 200:
            r2 = await client.post(f"{url}/agent/start", json={"working_dir": "/tmp"}, headers=headers)
            print(f"POST /agent/start: {r2.status_code} {r2.text[:200]}")
            data = r2.json()
            session_id = data.get("id") or data.get("session_id") or session_id
            r3 = await client.post(f"{url}/agent/resume", json={"session_id": session_id, "load_model_and_extensions": True}, headers=headers)
            print(f"POST /agent/resume: {r3.status_code} {r3.text[:200]}")
        else:
            r3 = await client.post(f"{url}/agent/resume", json={"session_id": session_id, "load_model_and_extensions": True}, headers=headers)
            print(f"POST /agent/resume (existing): {r3.status_code} {r3.text[:200]}")
        
        # Send message via /reply
        payload = {
            "session_id": session_id,
            "user_message": {
                "role": "user",
                "created": int(time.time()),
                "content": [{"type": "text", "text": "Decime solo: Hola, LINA aca. Todo OK con el Comm Bridge."}],
                "metadata": {"userVisible": True, "agentVisible": True},
            },
        }
        
        print(f"\nSending to /reply...")
        texts = []
        async with client.stream("POST", f"{url}/reply", json=payload, headers=headers) as resp:
            print(f"POST /reply: {resp.status_code}")
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                raw = line[6:]
                if not raw.strip(): continue
                try:
                    data = json.loads(raw)
                    t = data.get("type", "")
                    if t == "Message":
                        msg = data.get("message", {})
                        for item in msg.get("content", []):
                            ct = item.get("type", "")
                            if ct == "text":
                                texts.append(item.get("text", ""))
                                print(f"  TEXT[{len(texts)}]: {item.get('text', '')[:200]}")
                            elif ct == "thinking":
                                print(f"  THINKING: {item.get('thinking', '')[:80]}...")
                            elif ct in ("tool_use", "tool_request", "toolRequest"):
                                tc = item.get("tool_call") or item.get("toolCall") or item
                                nm = tc.get("name", "") or tc.get("tool_name", "")
                                print(f"  TOOL: {nm}")
                            elif ct in ("tool_result", "tool_response", "toolResponse"):
                                print(f"  TOOL_RESULT")
                    elif t == "Finish":
                        reason = data.get("reason", "")
                        ts = data.get("token_state", {})
                        print(f"  FINISH: {reason} tokens={ts.get('totalTokens', 0)}")
                        break
                    elif t == "Ping":
                        pass
                    elif t == "Error":
                        print(f"  ERROR: {data.get('error', '')}")
                except json.JSONDecodeError:
                    pass
        
        full = "\n".join(texts)
        print(f"\n--- RESPONSE ({len(full)} chars) ---")
        print(full[:500])
    
    return "done"

asyncio.run(test())
