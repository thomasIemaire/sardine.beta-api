# Sardine Beta API

Backend API built with **FastAPI**, **MongoDB** (Motor + Beanie), and **JWT authentication**.

## Architecture

```
app/
├── main.py              # Entry point, lifespan, CORS, routers, static files
├── config.py            # Pydantic settings (.env)
├── database.py          # Motor client & Beanie init
├── core/
│   ├── enums.py         # UserRole, Status, TeamMemberRole, FlowStatus, etc.
│   ├── validators.py    # Password policy validation
│   ├── security.py      # JWT & password hashing
│   ├── exceptions.py    # Reusable HTTP exceptions
│   ├── audit.py         # Audit logging service
│   ├── membership.py    # Shared org membership check
│   └── avatar.py        # Pastel gradient avatar generator
└── features/
    ├── auth/            # Registration, login, JWT, brute force protection
    │   ├── models.py    # User, TokenBlacklist
    │   ├── schemas.py
    │   ├── service.py
    │   ├── dependencies.py
    │   └── router.py
    ├── users/           # Profile, admin user management
    │   ├── schemas.py
    │   ├── service.py
    │   └── router.py
    ├── organizations/   # Orgs, invitations
    │   ├── models.py    # Organization
    │   ├── schemas.py
    │   ├── service.py
    │   └── router.py
    ├── folders/         # Folder tree, soft delete, trash, retention
    │   ├── models.py    # Folder
    │   ├── schemas.py
    │   ├── service.py
    │   └── router.py
    ├── teams/           # Teams, members, hierarchy, inheritance
    │   ├── models.py    # Team, TeamMember, TeamHierarchy
    │   ├── schemas.py
    │   ├── service.py
    │   └── router.py
    ├── notifications/   # Real-time notifications (REST + WebSocket)
    │   ├── models.py    # Notification
    │   ├── schemas.py
    │   ├── service.py
    │   ├── ws_manager.py
    │   └── router.py
    ├── agents/          # Agents with git-like schema versioning
    │   ├── models.py    # Agent, AgentVersion, AgentShare
    │   ├── schemas.py
    │   ├── service.py
    │   └── router.py
    ├── flows/           # Flows with git-like data versioning
    │   ├── models.py    # Flow, FlowVersion, FlowShare
    │   ├── schemas.py
    │   ├── service.py
    │   └── router.py
    ├── permissions/     # Folder access rights (team + individual)
    │   ├── models.py    # FolderTeamPermission, FolderMemberPermission
    │   ├── schemas.py
    │   ├── service.py
    │   └── router.py
    └── audit/
        └── models.py    # AuditLog
```

## Quick start

### Prerequisites

- Python 3.12+
- MongoDB running locally (default: `mongodb://localhost:27017`)

### Installation

```bash
python -m venv .venv
source .venv/bin/activate   # Linux/macOS

Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass  # Windows
.venv\Scripts\activate                                      # Windows

pip install -e ".[dev]"

cp .env.example .env
# Edit .env — change SECRET_KEY to a random value
```

### Run

```bash
uvicorn app.main:app --reload
```

API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

## API Endpoints

### Auth
| Method | Endpoint                       | Auth   | Description                    |
|--------|--------------------------------|--------|--------------------------------|
| POST   | `/api/auth/register`           | -      | Register + auto private org + avatar |
| POST   | `/api/auth/login`              | -      | Login + JWT                    |
| POST   | `/api/auth/change-password`    | Bearer | Change password                |
| POST   | `/api/auth/logout`             | Bearer | Logout (blacklist token)       |
| POST   | `/api/auth/forgot-password`    | -      | Request password reset         |
| POST   | `/api/auth/reset-password`     | -      | Reset password with token      |
| POST   | `/api/auth/verify-email`       | -      | Verify email with token        |
| POST   | `/api/auth/resend-verification`| Bearer | Resend verification email      |

### Users
| Method | Endpoint                      | Auth   | Description                    |
|--------|-------------------------------|--------|--------------------------------|
| GET    | `/api/users/me`               | Bearer | Get current profile            |
| PATCH  | `/api/users/me`               | Bearer | Update profile                 |
| GET    | `/api/users/admin/list`       | Admin  | List all users (paginated)     |
| PATCH  | `/api/users/admin/{user_id}`  | Admin  | Update user status/role        |

### Organizations
| Method | Endpoint                              | Auth   | Description                |
|--------|---------------------------------------|--------|----------------------------|
| GET    | `/api/organizations/`                 | Bearer | List my organizations      |
| POST   | `/api/organizations/`                 | Bearer | Create organization        |
| PATCH  | `/api/organizations/{org_id}`         | Bearer | Update organization        |
| POST   | `/api/organizations/{org_id}/invite`  | Bearer | Invite user to org         |

### Folders
| Method | Endpoint                                              | Auth   | Description            |
|--------|-------------------------------------------------------|--------|------------------------|
| POST   | `/api/organizations/{org_id}/folders/`                | Bearer | Create subfolder       |
| GET    | `/api/organizations/{org_id}/folders/{id}/contents`   | Bearer | List folder contents   |
| GET    | `/api/organizations/{org_id}/folders/{id}/breadcrumb` | Bearer | Get breadcrumb         |
| PATCH  | `/api/organizations/{org_id}/folders/{id}/rename`     | Bearer | Rename folder          |
| PATCH  | `/api/organizations/{org_id}/folders/{id}/move`       | Bearer | Move folder            |
| DELETE | `/api/organizations/{org_id}/folders/{id}`            | Bearer | Soft delete (trash)    |
| GET    | `/api/organizations/{org_id}/folders/trash`           | Bearer | View trash             |
| POST   | `/api/organizations/{org_id}/folders/{id}/restore`    | Bearer | Restore from trash     |
| DELETE | `/api/organizations/{org_id}/folders/trash/empty`     | Bearer | Empty trash            |

### Teams
| Method | Endpoint                                                          | Auth   | Description            |
|--------|-------------------------------------------------------------------|--------|------------------------|
| POST   | `/api/organizations/{org_id}/teams/`                              | Bearer | Create team            |
| GET    | `/api/organizations/{org_id}/teams/`                              | Bearer | List my teams          |
| PATCH  | `/api/organizations/{org_id}/teams/{team_id}`                     | Bearer | Rename team            |
| DELETE | `/api/organizations/{org_id}/teams/{team_id}`                     | Bearer | Delete team            |
| POST   | `/api/organizations/{org_id}/teams/{team_id}/members`             | Bearer | Add member             |
| GET    | `/api/organizations/{org_id}/teams/{team_id}/members`             | Bearer | List members           |
| PATCH  | `.../teams/{team_id}/members/{uid}/role`                          | Bearer | Change member role     |
| PATCH  | `.../teams/{team_id}/members/{uid}/status`                        | Bearer | Toggle member status   |
| POST   | `/api/organizations/{org_id}/teams/sub-teams`                     | Bearer | Create sub-team        |
| GET    | `/api/organizations/{org_id}/teams/tree`                          | Bearer | Team hierarchy tree    |

### Notifications
| Method | Endpoint                                    | Auth   | Description                     |
|--------|---------------------------------------------|--------|---------------------------------|
| GET    | `/api/notifications/`                       | Bearer | List notifications (filterable) |
| GET    | `/api/notifications/unread-count`           | Bearer | Unread count (total/info/action)|
| PATCH  | `/api/notifications/{id}/read`              | Bearer | Mark as read                    |
| PATCH  | `/api/notifications/read-all`               | Bearer | Mark all as read                |
| POST   | `/api/notifications/{id}/resolve`           | Bearer | Resolve action notification     |
| DELETE | `/api/notifications/{id}`                   | Bearer | Delete notification             |
| WS     | `/api/notifications/ws?token=<jwt>`         | Token  | Real-time WebSocket             |

### Agents
| Method | Endpoint                                                    | Auth   | Description                  |
|--------|-------------------------------------------------------------|--------|------------------------------|
| POST   | `/api/organizations/{org_id}/agents/`                       | Bearer | Create agent + first version |
| GET    | `/api/organizations/{org_id}/agents/`                       | Bearer | List my agents               |
| GET    | `/api/organizations/{org_id}/agents/shared`                 | Bearer | List shared agents           |
| GET    | `/api/organizations/{org_id}/agents/{id}`                   | Bearer | Get agent detail             |
| PATCH  | `/api/organizations/{org_id}/agents/{id}`                   | Bearer | Update name/description      |
| DELETE | `/api/organizations/{org_id}/agents/{id}`                   | Bearer | Delete agent + versions      |
| POST   | `/api/organizations/{org_id}/agents/{id}/versions`          | Bearer | Create new version (branch)  |
| GET    | `/api/organizations/{org_id}/agents/{id}/versions`          | Bearer | List all versions (tree)     |
| GET    | `.../agents/{id}/versions/{vid}`                            | Bearer | Get version detail           |
| PATCH  | `/api/organizations/{org_id}/agents/{id}/active-version`    | Bearer | Checkout version             |
| GET    | `.../agents/{id}/versions/{vid}/history`                    | Bearer | Version ancestry (git log)   |
| POST   | `/api/organizations/{org_id}/agents/{id}/shares`            | Bearer | Share with orgs              |
| GET    | `/api/organizations/{org_id}/agents/{id}/shares`            | Bearer | List shares                  |
| DELETE | `.../agents/{id}/shares/{org_id}`                           | Bearer | Remove share                 |
| POST   | `/api/organizations/{org_id}/agents/fork/{id}`              | Bearer | Fork shared agent            |

### Flows
| Method | Endpoint                                                   | Auth   | Description                  |
|--------|-------------------------------------------------------------|--------|------------------------------|
| POST   | `/api/organizations/{org_id}/flows/`                       | Bearer | Create flow + first version  |
| GET    | `/api/organizations/{org_id}/flows/`                       | Bearer | List my flows                |
| GET    | `/api/organizations/{org_id}/flows/shared`                 | Bearer | List shared flows            |
| GET    | `/api/organizations/{org_id}/flows/{id}`                   | Bearer | Get flow detail              |
| PATCH  | `/api/organizations/{org_id}/flows/{id}`                   | Bearer | Update name/desc/status      |
| DELETE | `/api/organizations/{org_id}/flows/{id}`                   | Bearer | Delete flow + versions       |
| POST   | `/api/organizations/{org_id}/flows/{id}/versions`          | Bearer | Create new version (branch)  |
| GET    | `/api/organizations/{org_id}/flows/{id}/versions`          | Bearer | List all versions (tree)     |
| GET    | `.../flows/{id}/versions/{vid}`                            | Bearer | Get version detail           |
| PATCH  | `/api/organizations/{org_id}/flows/{id}/active-version`    | Bearer | Checkout version             |
| GET    | `.../flows/{id}/versions/{vid}/history`                    | Bearer | Version ancestry (git log)   |
| POST   | `/api/organizations/{org_id}/flows/{id}/shares`            | Bearer | Share with orgs              |
| GET    | `/api/organizations/{org_id}/flows/{id}/shares`            | Bearer | List shares                  |
| DELETE | `.../flows/{id}/shares/{org_id}`                           | Bearer | Remove share                 |
| POST   | `/api/organizations/{org_id}/flows/fork/{id}`              | Bearer | Fork shared flow             |

### Permissions
| Method | Endpoint                                                         | Auth   | Description                      |
|--------|------------------------------------------------------------------|--------|----------------------------------|
| PUT    | `/api/organizations/{org_id}/permissions/teams`                  | Bearer | Set team folder permission       |
| DELETE | `.../permissions/teams/{tid}/folders/{fid}`                      | Bearer | Remove team permission           |
| PUT    | `/api/organizations/{org_id}/permissions/members`                | Bearer | Set member individual permission |
| GET    | `.../permissions/teams/{tid}/members/{uid}`                      | Bearer | View member permissions detail   |
| GET    | `.../permissions/effective/users/{uid}/folders/{fid}`            | Bearer | Effective right on a folder      |
| GET    | `.../permissions/effective/users/{uid}`                          | Bearer | All effective rights for user    |
| GET    | `.../permissions/teams/{tid}/matrix`                             | Bearer | Team permissions matrix          |
| GET    | `.../permissions/folders/{fid}/access`                           | Bearer | Who has access to folder         |
| GET    | `.../permissions/teams/{tid}/folders/{fid}/cascade-impact`       | Bearer | Preview cascade impact           |

### Static Files
| Method | Endpoint                           | Auth | Description              |
|--------|------------------------------------|------|--------------------------|
| GET    | `/storage/avatars/{user_id}.webp`  | -    | User profile picture     |

### Health
| Method | Endpoint   | Auth | Description  |
|--------|------------|------|--------------|
| GET    | `/health`  | -    | Health check |

## Tech stack

- **Python 3.12+**
- **FastAPI** — async web framework
- **MongoDB** — NoSQL database
- **Motor** — async MongoDB driver
- **Beanie** — async MongoDB ODM (Pydantic v2)
- **python-jose** — JWT tokens
- **passlib[bcrypt]** — password hashing
- **Pillow** — avatar generation (pastel gradients)
- **Ruff** — linting & formatting
