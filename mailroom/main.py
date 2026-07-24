from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import sqlite3
import hashlib
import json


app = FastAPI()


PROFILE = "ga5-mailroom-action-gate/v2"


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
fingerprint TEXT,
response TEXT
)
""")


db.commit()



def canonical(data):

    return json.dumps(
        data,
        sort_keys=True,
        separators=(",",":")
    )



def fingerprint(data):

    return hashlib.sha256(
        canonical(data).encode()
    ).hexdigest()



def stable_action_id(package_id, fp):

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

    evaluationId:str | None = None

    dossiers:list = Field(default_factory=list)

    receipts:list = Field(default_factory=list)

    inputDigest:str | None = None




def get_package_id(dossier):

    return (
        dossier.get("dossierId")
        or
        dossier.get("packageId")
        or
        dossier.get("id")
        or
        "unknown"
    )




def get_evidence(dossier):

    refs=[]

    for source in dossier.get("sources",[]):

        for line in source.get("lines",[]):

            if isinstance(line,dict):

                if line.get("lineId"):

                    refs.append(
                        line["lineId"]
                    )


    if refs:
        return refs[:5]


    return [
        "dossier"
    ]




# YOUR LOGIC KEPT

def decide_action(dossier):

    text=json.dumps(
        dossier
    ).lower()



    if (
        "ignore previous" in text
        or
        "prompt injection" in text
        or
        "vault" in text
        or
        "secret" in text
    ):

        action="quarantine_item"



    elif (
        "duplicate" in text
        or
        "already paid" in text
        or
        "completed" in text
    ):

        action="no_action"



    elif (
        "approved" in text
        and
        "recipient" in text
    ):

        action="send_approved_notice"



    elif (
        "identity" in text
        or
        "ambiguous" in text
    ):

        action="request_confirmation"



    elif (
        "update" in text
        or
        "change" in text
    ):

        action="update_internal_record"



    else:

        action="create_draft"



    package_id=get_package_id(
        dossier
    )


    fp=fingerprint(
        dossier
    )


    return {


        "packageId":
            package_id,


        "actionId":
            stable_action_id(
                package_id,
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
            get_evidence(
                dossier
            ),


        "rationale":
            (
                action
                +
                " selected using evidence "
                +
                ",".join(
                    get_evidence(dossier)
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



        request_digest=fingerprint(
            req.dossiers
        )



        old=db.execute(
            """
            SELECT fingerprint,response
            FROM evaluations
            WHERE evaluationId=?
            """,
            (
                req.evaluationId,
            )
        ).fetchone()



        if old:


            if old[0]!=request_digest:

                raise HTTPException(
                    409,
                    "IDEMPOTENCY_CONFLICT"
                )


            return json.loads(
                old[1]
            )



        proposals=[]



        for dossier in req.dossiers:


            fp=fingerprint(
                dossier
            )



            cached=db.execute(
                """
                SELECT data
                FROM proposals
                WHERE fingerprint=?
                """,
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
                    """
                    INSERT INTO proposals
                    VALUES (?,?)
                    """,
                    (
                        fp,
                        json.dumps(result)
                    )
                )


            proposals.append(
                result
            )



        response={


            "profile":
                PROFILE,


            "evaluationId":
                req.evaluationId,


            "status":
                "awaiting_receipts",


            "inputDigest":
                request_digest,


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
                request_digest,
                json.dumps(response)
            )
        )


        db.commit()



        return response




    elif req.operation=="commit":


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



        saved=json.loads(
            row[0]
        )


        valid_ids={

            p["actionId"]
            for p in saved["proposals"]

        }



        for receipt in req.receipts:


            if receipt.get("actionId") not in valid_ids:

                raise HTTPException(
                    409,
                    "invalid receipt"
                )



        return {


            "profile":
                PROFILE,


            "evaluationId":
                req.evaluationId,


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