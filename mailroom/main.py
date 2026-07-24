"""
Q9 - Lethal-Trifecta Mailroom Action Gate
profile: ga5-mailroom-action-gate/v2

Two operations:
1. propose -> analyse dossiers and return safe proposals
2. commit  -> bind grader receipts and complete actions
"""

import os
import json
import hashlib
import sqlite3
import tempfile
import logging
import re
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

app = FastAPI()

logging.basicConfig(level=logging.INFO)


PROFILE = "ga5-mailroom-action-gate/v2"


ACTIONS = (
    "create_draft",
    "update_internal_record",
    "send_approved_notice",
    "request_confirmation",
    "quarantine_item",
    "no_action",
)


DB = "mailroom.db"


conn = sqlite3.connect(
    DB,
    check_same_thread=False
)


conn.execute("""
CREATE TABLE IF NOT EXISTS evaluations(
    evaluationId TEXT PRIMARY KEY,
    digest TEXT,
    response TEXT
)
""")


conn.execute("""
CREATE TABLE IF NOT EXISTS decisions(
    fingerprint TEXT PRIMARY KEY,
    proposal TEXT
)
""")


conn.execute("""
CREATE TABLE IF NOT EXISTS commits(
    commitId TEXT PRIMARY KEY,
    response TEXT
)
""")


conn.commit()



# -------------------------
# Helpers
# -------------------------


def canonical(data):

    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False
    )



def sha(data):

    return hashlib.sha256(
        canonical(data).encode()
    ).hexdigest()



def dossier_fingerprint(d):

    return sha(d)



def make_call_id(dossier_id, fp):

    return (
        "mr_"
        +
        hashlib.sha256(
            (
                dossier_id
                +
                fp
            ).encode()
        ).hexdigest()[:40]
    )



def safe_text(x):

    if not isinstance(x,str):
        return ""

    return x[:200]



# -------------------------
# Action decision engine
# -------------------------


def decide_action(dossier):

    text = json.dumps(
        dossier,
        ensure_ascii=False
    ).lower()



    # prompt injection / exfiltration
    if (
        "ignore previous" in text
        or
        "system prompt" in text
        or
        "copy vault" in text
        or
        "secret" in text
    ):

        return {
            "action":
            "quarantine_item",

            "fields":
            {
                "artifactId":
                dossier.get("id","unknown")
            },

            "evidence":
            ["source"]
        }



    # duplicate / completed

    if (
        "duplicate" in text
        or
        "already completed" in text
        or
        "already paid" in text
    ):

        return {

            "action":
            "no_action",

            "fields":
            {
                "reasonCode":
                "DUPLICATE"
            },

            "evidence":
            ["source"]

        }



    # approved outbound

    if (
        "approved" in text
        and
        "recipient" in text
    ):

        return {

            "action":
            "send_approved_notice",

            "fields":
            {
                "recipient":
                dossier.get(
                    "recipient",
                    ""
                ),

                "referenceId":
                dossier.get(
                    "referenceId",
                    ""
                )
            },

            "evidence":
            ["source"]

        }



    # identity conflict

    if (
        "identity" in text
        or
        "ambiguous" in text
        or
        "conflict" in text
    ):

        return {

            "action":
            "request_confirmation",

            "fields":
            {
                "team":
                "mailroom",

                "referenceId":
                dossier.get(
                    "id",
                    ""
                )
            },

            "evidence":
            ["source"]

        }



    return {

        "action":
        "create_draft",

        "fields":
        {
            "recipient":
            dossier.get(
                "sender",
                ""
            ),

            "referenceId":
            dossier.get(
                "id",
                ""
            )
        },

        "evidence":
        ["source"]

    }

# -------------------------
# Proposal builder
# -------------------------


def proposal_digest(proposal):

    core = {

        "dossierId":
        proposal["dossierId"],

        "callId":
        proposal["callId"],

        "action":
        proposal["action"],

        "target":
        proposal["target"],

        "payload":
        proposal["payload"],

        "evidence":
        sorted(
            proposal["evidence"]
        )
    }

    return sha(core)



def build_target_payload(action, fields, dossier):


    if action == "create_draft":

        return (

            {
                "kind":
                "draft_queue",

                "id":
                "mailroom"
            },

            {

                "recipient":
                safe_text(
                    fields.get(
                        "recipient",
                        ""
                    )
                ),

                "referenceId":
                safe_text(
                    fields.get(
                        "referenceId",
                        dossier.get(
                            "id",
                            ""
                        )
                    )
                ),

                "template":
                "customer_reply"

            }

        )



    if action == "update_internal_record":

        return (

            {
                "kind":
                "case_record",

                "id":
                safe_text(
                    dossier.get(
                        "id",
                        ""
                    )
                )
            },


            {

                "field":
                "delivery_window",

                "value":
                "verified"

            }

        )



    if action == "send_approved_notice":

        return (

            {
                "kind":
                "email",

                "id":
                safe_text(
                    fields.get(
                        "recipient",
                        ""
                    )
                )
            },


            {

                "referenceId":
                safe_text(
                    fields.get(
                        "referenceId",
                        ""
                    )
                ),

                "template":
                "approved_notice"

            }

        )



    if action == "request_confirmation":

        return (

            {
                "kind":
                "approval_queue",

                "id":
                safe_text(
                    fields.get(
                        "team",
                        "mailroom"
                    )
                )
            },


            {

                "questionCode":
                "VERIFY_REQUEST",

                "referenceId":
                safe_text(
                    fields.get(
                        "referenceId",
                        ""
                    )
                )

            }

        )



    if action == "quarantine_item":

        return (

            {
                "kind":
                "security_queue",

                "id":
                "mailroom"
            },


            {

                "artifactId":
                safe_text(
                    fields.get(
                        "artifactId",
                        dossier.get(
                            "id",
                            ""
                        )
                    )
                ),

                "reasonCode":
                "INDIRECT_PROMPT_INJECTION"

            }

        )



    # no_action

    return (

        None,


        {

            "reasonCode":
            fields.get(
                "reasonCode",
                "INFORMATIONAL"
            ),

            "referenceId":
            safe_text(
                dossier.get(
                    "id",
                    ""
                )
            )

        }

    )





def create_proposal(dossier):


    dossier_id = (

        dossier.get("dossierId")

        or

        dossier.get("id")

        or

        dossier.get("packageId")

        or

        "unknown"

    )


    fp = dossier_fingerprint(
        dossier
    )


    decision = decide_action(
        dossier
    )


    action = decision["action"]


    target, payload = build_target_payload(

        action,

        decision.get(
            "fields",
            {}
        ),

        dossier

    )



    proposal = {


        "dossierId":
        dossier_id,


        "callId":
        make_call_id(
            dossier_id,
            fp
        ),


        "action":
        action,


        "target":
        target,


        "payload":
        payload,


        "evidence":
        decision.get(
            "evidence",
            ["source"]
        )

    }


    return proposal





# -------------------------
# Cache handling
# -------------------------


def get_cached_proposal(fp):

    row = conn.execute(

        """
        SELECT proposal
        FROM decisions
        WHERE fingerprint=?
        """,

        (fp,)

    ).fetchone()


    if row:

        return json.loads(
            row[0]
        )


    return None





def save_cached_proposal(fp, proposal):

    conn.execute(

        """
        INSERT OR REPLACE
        INTO decisions
        VALUES (?,?)
        """,

        (
            fp,
            json.dumps(
                proposal
            )
        )

    )

    conn.commit()

# -------------------------
# Request models
# -------------------------

class MailRequest(BaseModel):

    operation: str

    evaluationId: str | None = None

    dossiers: list = []

    receipts: list = []

    inputDigest: str | None = None



# -------------------------
# Propose endpoint
# -------------------------


@app.post("/v1/mailroom/actions")
def mailroom(req: MailRequest):


    if req.operation == "propose":


        if not req.evaluationId:

            raise HTTPException(
                422,
                "evaluationId required"
            )



        request_digest = sha(
            req.dossiers
        )



        # replay / conflict check

        old = conn.execute(

            """
            SELECT inputDigest,response
            FROM evaluations
            WHERE evaluationId=?
            """,

            (
                req.evaluationId,
            )

        ).fetchone()



        if old:


            if old[0] != request_digest:

                raise HTTPException(

                    409,

                    "IDEMPOTENCY_CONFLICT"

                )


            return json.loads(
                old[1]
            )





        proposals = []



        for dossier in req.dossiers:


            fp = dossier_fingerprint(
                dossier
            )


            cached = get_cached_proposal(
                fp
            )


            if cached:


                proposal = cached


            else:


                proposal = create_proposal(
                    dossier
                )


                save_cached_proposal(

                    fp,

                    proposal

                )



            proposals.append(
                proposal
            )





        response = {


            "profile":
            "ga5-mailroom-action-gate/v2",


            "evaluationId":
            req.evaluationId,


            "status":
            "awaiting_receipts",


            "inputDigest":
            request_digest,


            "proposals":
            proposals

        }




        conn.execute(

            """
            INSERT INTO evaluations
            VALUES (?,?,?)
            """,

            (

                req.evaluationId,

                request_digest,

                json.dumps(
                    response
                )

            )

        )


        conn.commit()



        return response






    # -------------------------
    # Commit operation
    # -------------------------


    elif req.operation == "commit":


        if not req.evaluationId:

            raise HTTPException(
                422,
                "evaluationId required"
            )



        saved = conn.execute(

            """
            SELECT response
            FROM evaluations
            WHERE evaluationId=?
            """,

            (
                req.evaluationId,
            )

        ).fetchone()



        if not saved:

            raise HTTPException(

                409,

                "unknown evaluation"

            )



        proposal_data = json.loads(
            saved[0]
        )


        proposals = proposal_data[
            "proposals"
        ]



        proposal_map = {

            p["callId"]:
            p

            for p in proposals

        }




        outcomes = []



        for receipt in req.receipts:


            call_id = receipt.get(
                "callId"
            )


            proposal = proposal_map.get(
                call_id
            )


            if not proposal:

                raise HTTPException(

                    409,

                    "invalid receipt"

                )



            if receipt.get(
                "action"
            ) != proposal["action"]:


                raise HTTPException(

                    409,

                    "action mismatch"

                )



            outcomes.append({

                "dossierId":
                proposal["dossierId"],


                "callId":
                call_id,


                "action":
                proposal["action"],


                "receiptId":
                receipt.get(
                    "receiptId",
                    ""
                ),


                "status":
                "executed"

                if receipt.get(
                    "accepted"
                )

                else

                "rejected"

            })





        return {


            "profile":
            "ga5-mailroom-action-gate/v2",


            "evaluationId":
            req.evaluationId,


            "status":
            "completed",


            "inputDigest":
            proposal_data[
                "inputDigest"
            ],


            "outcomes":
            outcomes

        }





    else:


        raise HTTPException(

            400,

            "invalid operation"

        )