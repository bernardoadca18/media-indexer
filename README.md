# Media Indexer

Camada de entendimento e memória audiovisual do NeoVids. Registra projetos e
sessões, preserva notas brutas, armazena mídias por identidade lógica e extrai
metadata técnica com ffprobe em um job assíncrono.

## Quick start

    cp .env.example .env
    docker compose up --build

API: http://localhost:8012 (OpenAPI em /docs).

    PROJECT_ID=$(curl -s -X POST http://localhost:8012/projects -H 'Content-Type: application/json' -d '{"name":"The Witcher 1 Playthrough"}' | jq -r .id)
    curl -X POST "http://localhost:8012/projects/$PROJECT_ID/sessions" -F 'label=Session 001' -F 'video=@001.mp4' -F 'notes=@001.txt'

Consulte GET /jobs/{job_id} e GET /sessions/{session_id}.

## Desenvolvimento

    cd backend
    pip install -r requirements-dev.txt
    alembic upgrade head
    pytest
    ruff check app tests
    mypy app
