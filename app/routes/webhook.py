from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.order_service import process_incoming_order
from app.services.reporter import (
    send_morning_report,
    send_evening_report
)

from app.services.product_catalog import (
    generate_menu_template
)

from app.services.notifier import (
    send_whatsapp_message
)

router = APIRouter()


@router.get("/health")
def health_check():

    return {
        "status": "OrdeRR webhook is running"
    }


@router.post("/test")
async def test_webhook(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Test endpoint for Postman/manual testing.
    """

    try:

        payload = await request.json()

        customer_phone = payload.get(
            "phone",
            "919999999999"
        )

        message_text = payload.get(
            "message",
            ""
        )

        if not message_text:

            return {
                "status": "error",
                "message": "No message provided"
            }

        result = process_incoming_order(
            db=db,
            customer_phone=customer_phone,
            message=message_text
        )

        return {
            "status": "success",
            "order_id": result["order_id"],
            "customer": customer_phone,
            "parsed": result["parsed"],
            "is_unclear": result["parsed"].get(
                "is_unclear",
                False
            )
        }

    except Exception as e:

        return {
            "status": "error",
            "message": str(e)
        }


@router.post("/report/morning")
def trigger_morning_report(
    db: Session = Depends(get_db)
):

    send_morning_report(db)

    return {
        "status": "Morning report sent"
    }


@router.post("/report/evening")
def trigger_evening_report(
    db: Session = Depends(get_db)
):

    send_evening_report(db)

    return {
        "status": "Evening report sent"
    }


@router.get("/meta")
async def meta_webhook_verify(request: Request):
    """
    Meta webhook verification
    """

    mode = request.query_params.get("hub.mode")

    token = request.query_params.get(
        "hub.verify_token"
    )

    challenge = request.query_params.get(
        "hub.challenge"
    )

    if (
        mode == "subscribe"
        and token == "orderr_fluffy_2026"
    ):

        return int(challenge)

    return {
        "error": "Invalid token"
    }


@router.post("/meta")
async def meta_webhook(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Receives incoming WhatsApp messages
    from Meta Cloud API
    """

    try:

        payload = await request.json()

        for entry in payload.get("entry", []):

            for change in entry.get("changes", []):

                value = change.get("value", {})

                messages = value.get(
                    "messages",
                    []
                )

                for message in messages:

                    customer_phone = message.get(
                        "from",
                        ""
                    )

                    message_type = message.get(
                        "type",
                        ""
                    )

                    is_photo = False

                    if message_type == "text":

                        message_text = message.get(
                            "text",
                            {}
                        ).get(
                            "body",
                            ""
                        )

                    elif message_type == "image":

                        message_text = message.get(
                            "image",
                            {}
                        ).get(
                            "caption",
                            "Photo order received"
                        )

                        is_photo = True

                    else:
                        continue

                    if (
                        not message_text
                        or not customer_phone
                    ):
                        continue

                    # CLEAN MESSAGE
                    message_text_clean = (
                        message_text
                        .strip()
                        .lower()
                    )

                    # SEND ORDER TEMPLATE
                    if message_text_clean == "order":

                        menu_template = (
                            generate_menu_template()
                        )

                        send_whatsapp_message(
                            customer_phone,
                            menu_template
                        )

                        continue

                    # PROCESS NORMAL ORDER
                    process_incoming_order(
                        db=db,
                        customer_phone=customer_phone,
                        message=message_text,
                        is_photo=is_photo
                    )

        return {
            "status": "ok"
        }

    except Exception as e:

        return {
            "status": "error",
            "message": str(e)
        }