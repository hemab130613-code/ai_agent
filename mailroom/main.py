from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import sqlite3
import hashlib
import json
import os


app = FastAPI()


PROFILE = "ga5-mailroom-action-gate/v2"


db = sqlite3.connect(
    "mailroom.db",
    check_same_thread=False
)


db.execute("""
CREATE TABLE IF NOT EXISTS evaluations(
evaluationId TEXT PRIMARY KEY,
digest TEXT,
response TEXT
)
""")


db.execute("""
CREATE TABLE IF NOT EXISTS proposals(
callId TEXT PRIMARY KEY,
data TEXT
)
""")


db.execute("""
CREATE TABLE IF NOT EXISTS commits(
digest TEXT PRIMARY KEY,
response TEXT
)
""")


db.commit()



def canonical(x):

    return json.dumps(
        x,
        sort_keys=True,
        separators=(",",":")
    )



def fingerprint(x):

    return hashlib.sha256(
        canonical(x).encode()
    ).hexdigest()



def call_id(package_id,fp):

    return (
        "mr_"
        +
        hashlib.sha256(
            (
                package_id
                +
                fp
            ).encode()
        ).hexdigest()[:40]
    )



class MailRequest(BaseModel):

    operation:str

    evaluationId:str|None=None

    dossiers:list=[]

    receipts:list=[]

    inputDigest:str|None=None



def get_id(d):

    return (
        d.get("dossierId")
        or
        d.get("packageId")
        or
        d.get("id")
        or
        "unknown"
    )



def evidence(d):

    refs=[]

    sources=d.get("sources",[])

    for s in sources:

        for l in s.get("lines",[])[:5]:

            if l.get("lineId"):

                refs.append(
                    l["lineId"]
                )

    return refs[:5] or ["source"]



def decide_action(dossier):


    text=json.dumps(
        dossier
    ).lower()



    if (
        "ignore previous"
        in text
        or
        "prompt injection"
        in text
        or
        "vault"
        in text
        or
        "secret"
        in text
    ):

        action="quarantine_item"



    elif (
        "duplicate"
        in text
        or
        "already paid"
        in text
        or
        "completed"
        in text
    ):

        action="no_action"



    elif (
        "approved"
        in text
        and
        "recipient"
        in text
    ):

        action="send_approved_notice"



    elif (
        "identity"
        in text
        or
        "ambiguous"
        in text
        or
        "conflict"
        in text
    ):

        action="request_confirmation"



    elif (
        "update"
        in text
        or
        "change"
        in text
    ):

        action="update_internal_record"



    else:

        action="create_draft"



    did=get_id(dossier)


    fp=fingerprint(dossier)



    return {

        "packageId":did,


        "actionId":
            call_id(
                did,
                fp
            ),


        "action":
            action,


        "facts":{

            "vendorName":
                dossier.get(
                    "vendorName",
                    ""
                ),

            "invoiceNumber":
                dossier.get(
                    "invoiceNumber",
                    ""
                ),

            "amountMinor":
                dossier.get(
                    "amountMinor",
                    0
                ),

            "currency":
                dossier.get(
                    "currency",
                    "INR"
                )
        },


        "evidenceRefs":
            evidence(dossier),


        "rationale":
            (
                action
                +
                " selected using "
                +
                ",".join(
                    evidence(dossier)
                )
            )
    }




@app.post("/v1/mailroom/actions")
def mailroom(req:MailRequest):


    if req.operation=="propose":


        if not req.evaluationId:

            raise HTTPException(
                422,
                "evaluationId required"
            )



        digest=fingerprint(
            req.dossiers
        )


        old=db.execute(
            """
            SELECT digest,response
            FROM evaluations
            WHERE evaluationId=?
            """,
            (
                req.evaluationId,
            )
        ).fetchone()



        if old:


            if old[0]!=digest:

                raise HTTPException(
                    409,
                    "IDEMPOTENCY_CONFLICT"
                )


            return json.loads(
                old[1]
            )



        proposals=[]


        for d in req.dossiers:


            result=decide_action(d)


            proposals.append(
                result
            )


            db.execute(
                """
                INSERT OR REPLACE INTO proposals
                VALUES (?,?)
                """,
                (
                    result["actionId"],
                    json.dumps(result)
                )
            )



        response={


            "profile":
                PROFILE,


            "evaluationId":
                req.evaluationId,


            "status":
                "awaiting_receipts",


            "inputDigest":
                digest,


            "proposals":
                proposals

        }



        db.execute(
            """
            INSERT INTO evaluations
            VALUES (?,?,?)
            """,
            (
                req.evaluationId,
                digest,
                json.dumps(response)
            )
        )


        db.commit()


        return response




    if req.operation=="commit":


        if not req.evaluationId:

            raise HTTPException(
                422,
                "evaluationId required"
            )



        row=db.execute(
            """
            SELECT response
            FROM evaluations
            WHERE evaluationId=?
            """,
            (
                req.evaluationId,
            )
        ).fetchone()



        if not row:

            raise HTTPException(
                409,
                "unknown evaluation"
            )



        stored=json.loads(
            row[0]
        )


        proposals={
            p["actionId"]:p
            for p in stored["proposals"]
        }



        outcomes=[]



        for r in req.receipts:


            aid=r.get(
                "actionId"
            )


            if aid not in proposals:

                raise HTTPException(
                    409,
                    "invalid receipt"
                )



            p=proposals[aid]



            if r.get(
                "action"
            ) != p["action"]:

                raise HTTPException(
                    409,
                    "action mismatch"
                )



            outcomes.append({

                "packageId":
                    p["packageId"],

                "actionId":
                    aid,

                "action":
                    p["action"],

                "receiptNonce":
                    r.get(
                        "receiptNonce"
                    )

            })



        response={

            "profile":
                PROFILE,

            "evaluationId":
                req.evaluationId,

            "status":
                "completed",

            "outcomes":
                outcomes

        }


        return response




    raise HTTPException(
        400,
        "invalid operation"
    )