import os
import requests

STRATZ_TOKEN = os.getenv("STRATZ_TOKEN")

def fetch_match_result(match_id):
    url = f"https://api.stratz.com/api/v1/match/{match_id}"
    headers = {
        "Authorization": f"Bearer {STRATZ_TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "STRATZ_API"
    }
    query = {
    "query": f"""
    query {{
      match(id: {match_id}) {{
        id
        didRadiantWin
        players {{
          steamAccountId
          isRadiant
        }}
      }}
    }}
    """
}
    
    response = requests.post(
    "https://api.stratz.com/graphql",
    json=query
)
    
     # 🔍 DEBUG: print status and response content
    print("STRATZ response code:", response.status_code)
    print("STRATZ response body:", response.text)

    if response.status_code == 200:
        data = response.json()
        match_data = data['data']['match']
        print(match_data)
    else:
        print("Failed to fetch data. Status:", response.status_code)

    return None
