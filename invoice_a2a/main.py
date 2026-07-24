from fastapi import FastAPI, Header, HTTPException
import sqlite3
import json
import uuid
import hashlib


app = FastAPI()


TOKEN = "123"


db = sqlite3.connect(
    "invoice.db",
    check_same_thread=False
)


db.execute("""
CREATE TABLE IF NOT EXISTS tasks(
id TEXT PRIMARY KEY,
principal TEXT,
data TEXT
)
""")

db.commit()



def get_hash(data):
    return hashlib.sha256(
        json.dumps(data, sort_keys=True).encode()
    ).hexdigest()



@app.get("/.well-known/agent-card.json")
def agent_card():

    return {

        "name": "Invoice Action Agent",

        "description":
        "AI agent for invoice reconciliation actions",

        "version": "1.0",

        "capabilities": {},

        "skills": [
            {
                "name": "invoice_action_agent",
                "description":
                "Reads invoice documents and proposes actions",
                "tags": [
                    "invoice",
                    "finance",
                    "reconciliation"
                ]
            }
        ],

        "supportedInterfaces": [
            {
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "1.0"
            }
        ],

        "defaultInputModes": [
            "application/vnd.ga5.invoice-claim-batch+json"
        ],

        "defaultOutputModes": [
            "application/vnd.ga5.invoice-action-proposals+json",
            "application/vnd.ga5.invoice-action-receipts+json"
        ]
    }




@app.post("/a2a/message:send")
def send_message(
    body: dict,
    authorization: str = Header(None),
    A2A_Version: str = Header(None)
):

    if authorization != "Bearer " + TOKEN:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )


    if A2A_Version != "1.0":
        raise HTTPException(
            status_code=400,
            detail="Wrong A2A version"
        )


    task_id = str(uuid.uuid4())


    task = {

        "id": task_id,

        "status": {
            "state":
            "TASK_STATE_INPUT_REQUIRED"
        },

        "history": [
            body
        ],

        "artifacts": [

            {

            "mediaType":
            "application/vnd.ga5.invoice-action-proposals+json",

            "data":
                {
                "batchId":"pending",
                "proposals":[]
                }

            }

        ]

    }


    db.execute(
        "INSERT INTO tasks VALUES (?,?,?)",
        (
            task_id,
            "default",
            json.dumps(task)
        )
    )

    db.commit()


    return {
        "task":task
    }





@app.get("/a2a/tasks/{task_id}")
def get_task(
    task_id:str,
    authorization:str=Header(None)
):

    if authorization!="Bearer "+TOKEN:
        raise HTTPException(401)


    row=db.execute(
        "SELECT data FROM tasks WHERE id=?",
        (task_id,)
    ).fetchone()


    if not row:
        raise HTTPException(404)


    return json.loads(row[0])





@app.get("/a2a/tasks")
def list_tasks(
    authorization:str=Header(None)
):

    if authorization!="Bearer "+TOKEN:
        raise HTTPException(401)


    rows=db.execute(
        "SELECT data FROM tasks"
    ).fetchall()


    return {
        "tasks":[
            json.loads(x[0])
            for x in rows
        ]
    }





@app.post("/a2a/tasks/{task_id}:cancel")
def cancel_task(
    task_id:str,
    authorization:str=Header(None)
):

    if authorization!="Bearer "+TOKEN:
        raise HTTPException(401)


    return {

        "id":task_id,

        "status":{
            "state":
            "TASK_STATE_CANCELED"
        }

    }