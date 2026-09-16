set shell := ["bash", "-cu"]

default:
    @just --list

install:
    uv sync --extra dev
    cd web && npm install

# run hub + local node against a dev database, with the SPA served from web/dist
dev:
    uv run agentdash hub --reload

node:
    uv run agentdash node

web:
    cd web && npm run dev

build-web:
    cd web && npm run build

test:
    uv run pytest -q

lint:
    uv run ruff check agentdash tests
    uv run ruff format --check agentdash tests

fmt:
    uv run ruff format agentdash tests
    uv run ruff check --fix agentdash tests
