from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sqlite3
import hashlib
import json
import uuid


app = FastAPI()


db = sqlite3.connect(
    "mailroom.db",
    check_same_thread=False
)


db.execute("""
CREATE TABLE IF NOT EXISTS proposals(
fingerprint TEXT PRIMARY KEY,
data TEXT
)
""")


db.execute("""
CREATE TABLE IF NOT EXISTS evaluations(
evaluationId TEXT PRIMARY KEY,
fingerprint TEXT
)
""")


db.commit()



def fingerprint(data):

    return hashlib.sha256(
        json.dumps(
            data,
            sort_keys=True
        ).encode()
    ).hexdigest()



class MailRequest(BaseModel):

    operation:str

    evaluationId:str | None=None

    dossiers:list=[]

    receipts:list=[]




def decide_action(dossier):

    text=json.dumps(dossier).lower()


    if "ignore previous" in text or "prompt injection" in text:

        action="quarantine_item"


    elif "duplicate" in text or "already paid" in text:

        action="no_action"


    elif "approved" in text and "recipient" in text:

        action="send_approved_notice"


    elif "identity" in text or "ambiguous" in text:

        action="request_confirmation"


    else:

        action="create_draft"



    package_id = dossier.get(
        "id",
        dossier.get(
            "packageId",
            "unknown"
        )
    )


    return {

        "packageId": package_id,


        "actionId":
        hashlib.sha256(
            json.dumps(dossier,sort_keys=True).encode()
        ).hexdigest(),


        "action":action,


        "facts":{

            "vendorName":
            dossier.get("vendorName",""),


            "invoiceNumber":
            dossier.get("invoiceNumber",""),


            "amountMinor":
            dossier.get("amountMinor",0),


            "currency":
            dossier.get("currency","INR")
        },


        "evidenceRefs":[
            "dossier"
        ],


        "rationale":
        f"{action} selected from dossier evidence."
    }




@app.post("/v1/mailroom/actions")
def mailroom(req:MailRequest):


    if req.operation=="propose":


        request_fp=fingerprint(
            req.dossiers
        )


        old=db.execute(
            "SELECT fingerprint FROM evaluations WHERE evaluationId=?",
            (req.evaluationId,)
        ).fetchone()


        if old and old[0]!=request_fp:

            raise HTTPException(
                status_code=409,
                detail="IDEMPOTENCY_CONFLICT"
            )


        if not old:

            db.execute(
                "INSERT INTO evaluations VALUES (?,?)",
                (
                    req.evaluationId,
                    request_fp
                )
            )

            db.commit()



        proposals=[]


        for dossier in req.dossiers:


            fp=fingerprint(dossier)


            cached=db.execute(
                "SELECT data FROM proposals WHERE fingerprint=?",
                (fp,)
            ).fetchone()



            if cached:

                result=json.loads(
                    cached[0]
                )


            else:

                result=decide_action(
                    dossier
                )


                db.execute(
                    "INSERT INTO proposals VALUES (?,?)",
                    (
                        fp,
                        json.dumps(result)
                    )
                )

                db.commit()



            proposals.append(result)



        return {

            "status":
            "awaiting_receipts",

            "proposals":
            proposals
        }




    elif req.operation=="commit":


        return {

            "status":
            "completed",

            "outcomes":
            req.receipts
        }



    else:

        raise HTTPException(
            400,
            "invalid operation"
        )