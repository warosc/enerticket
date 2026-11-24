-- Esquema de la Fase 1 (MVP) tal como lo creaba Base.metadata.create_all en PostgreSQL.
-- Lo usa la CI para probar que 0001_core renombra las tablas legacy (tablas, índices,
-- secuencias y restricciones) y que la cadena completa sube y baja en PostgreSQL.
CREATE TABLE tickets (
    id SERIAL NOT NULL,
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    status VARCHAR(30) NOT NULL,
    priority VARCHAR(20) NOT NULL,
    department VARCHAR(100) NOT NULL,
    requester VARCHAR(200) NOT NULL,
    assignee VARCHAR(200),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id)
);
CREATE TABLE comments (
    id SERIAL NOT NULL,
    ticket_id INTEGER NOT NULL,
    author VARCHAR(200) NOT NULL,
    body TEXT NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (ticket_id) REFERENCES tickets (id)
);
CREATE TABLE audit_events (
    id SERIAL NOT NULL,
    ticket_id INTEGER NOT NULL,
    actor VARCHAR(200) NOT NULL,
    action VARCHAR(80) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY (ticket_id) REFERENCES tickets (id)
);
INSERT INTO tickets (title, description, status, priority, department, requester, created_at)
VALUES ('Ticket de la Fase 1', '', 'new', 'medium', 'General', 'usuario.mvp', now());
INSERT INTO comments (ticket_id, author, body) VALUES (1, 'usuario.mvp', 'Comentario MVP');
INSERT INTO audit_events (ticket_id, actor, action, timestamp) VALUES (1, 'usuario.mvp', 'created', now());
