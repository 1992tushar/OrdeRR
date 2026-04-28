from fastapi import FastAPI
from app.database import engine, Base
from app.routes import webhook
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

# Create all database tables automatically
Base.metadata.create_all(bind=engine)

# Initialize FastAPI app
app = FastAPI(
    title="OrdeRR",
    description="WhatsApp Order Automation for Fluffy Plant",
    version="1.0.0"
)

# Register routes
app.include_router(webhook.router, prefix="/webhook", tags=["Webhook"])

# Health check endpoint
@app.get("/")
def root():
    return {
        "app": "OrdeRR",
        "plant": os.getenv("PLANT_NAME", "Fluffy"),
        "status": "running"
    }