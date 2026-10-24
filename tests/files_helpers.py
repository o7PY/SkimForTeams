from fake_graph import FakeGraph
from skim.graph.auth import StaticTokenProvider
from skim.graph.client import GraphClient
from skim.jobs.common import Budget, JobContext
from skim.jobs.files_sync import full_sync_channel
from skim.storage.memory import MemoryStorage

TEAM, CH, DRIVE, FOLDER = "19:team@thread.tacv2", "19:chan@thread.tacv2", "b!drive", "F0"
CH_ROW = {"id": CH, "team_id": TEAM, "files_drive_id": DRIVE, "files_folder_item_id": FOLDER}


def item(i, name, parent, folder=False, etag="e1", size=10):
    d = {"id": i, "name": name, "size": size, "eTag": etag, "cTag": "c" + etag,
         "webUrl": f"https://x.sharepoint.com/sites/T/Shared%20Documents/General/{name}",
         "createdDateTime": "2026-10-01T10:00:00Z", "lastModifiedDateTime": "2026-10-02T10:00:00Z",
         "createdBy": {"user": {"id": "u1", "displayName": "Prof"}}, "lastModifiedBy": {"user": {"id": "u1", "displayName": "Prof"}},
         "parentReference": {"driveId": DRIVE, "id": parent}}
    d.update({"folder": {"childCount": 1}} if folder else {"file": {"mimeType": "application/pdf"}})
    return d


def tree(fake, extra=None):
    fake.add(f"/drives/{DRIVE}/items/{FOLDER}/delta", {"value": [], "@odata.deltaLink": "{base}" + f"/drives/{DRIVE}/items/{FOLDER}/delta?token=START"})
    fake.add(f"/drives/{DRIVE}/items/{FOLDER}/children", {"value": [item("L", "Lectures", FOLDER, True), item("a", "syllabus.pdf", FOLDER)]})
    fake.add(f"/drives/{DRIVE}/items/L/children", {"value": [item("w3", "week3.pdf", "L"), item("S", "Slides", "L", True)],
                                                  "@odata.nextLink": "{base}/page2"})
    fake.add("/page2", {"value": [item("w4", "week4.pdf", "L")]})
    fake.add(f"/drives/{DRIVE}/items/S/children", {"value": [item("s1", "deep.pdf", "S")]})
    return fake


async def sync(fake, store, budget=60, clock=None):
    async with fake.serve() as base:
        g = GraphClient(StaticTokenProvider(), base_url=base)
        ctx = JobContext(store, g, Budget(budget))
        status = await full_sync_channel(ctx, await_channel(store))
        await g.close()
    return status, ctx


def await_channel(store):
    return store.channels[CH]


async def make_store():
    s = MemoryStorage()
    await s.upsert_team({"id": TEAM, "aad_group_id": "g"})
    await s.upsert_channel(CH_ROW)
    return s


