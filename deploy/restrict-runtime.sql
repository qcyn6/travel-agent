-- Run AFTER schema initialization/migration, before exposing the new deployment.
-- Applies only to the account created by deploy/compose.yaml.
-- Future schema migrations require a temporary maintenance grant by the DB admin.
REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'travel_app'@'%';
GRANT SELECT, INSERT, UPDATE, DELETE ON travel_agent.* TO 'travel_app'@'%';
