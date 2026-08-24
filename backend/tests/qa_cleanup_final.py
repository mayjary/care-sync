"""Cleanup of QA artifacts (test-created reports/documents). Does not touch seeded demo users."""
import asyncio, os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import dotenv_values

env = dotenv_values("/app/backend/.env")
client = AsyncIOMotorClient(env["MONGO_URL"])
db = client[env["DB_NAME"]]

async def main():
    r = await db.reports.delete_many({"title": {"$regex": "^TEST", "$options": "i"}})
    d = await db.patient_documents.delete_many({"title": {"$regex": "^TEST", "$options": "i"}})
    ex = await db.availability_exceptions.delete_many({"reason": {"$regex": "^TEST", "$options": "i"}})
    print("deleted reports:", r.deleted_count, "patient_documents:", d.deleted_count, "exceptions:", ex.deleted_count)
    print("remaining reports:", await db.reports.count_documents({}),
          "patient_documents:", await db.patient_documents.count_documents({}),
          "exceptions:", await db.availability_exceptions.count_documents({}))
    print("appointments by status:")
    async for row in db.appointments.aggregate([{"$group": {"_id": "$status", "n": {"$sum": 1}}}]):
        print("  ", row["_id"], row["n"])
    client.close()

asyncio.run(main())
