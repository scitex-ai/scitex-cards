-- Reviewed development incident recipe, NOT application startup/migration.
-- Use an authorized migration identity after read-only schema/ACL inspection.
-- Assumes all seven grants below are absent; aborts if that prestate changed.
BEGIN;
SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '10s';
DO $guard$
BEGIN
  IF current_database() <> 'scitex' THEN
    RAISE EXCEPTION 'Unexpected database; review the canonical target first';
  END IF;
  IF (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind='r'
      AND c.relname IN ('status_exchanges_rows','status_exchanges_oplog',
                       'status_exchanges_cursor','status_exchanges_identity')
      AND pg_get_userbyid(c.relowner)='scitex_store_owner'
      AND NOT c.relrowsecurity AND NOT c.relforcerowsecurity) <> 4 THEN
    RAISE EXCEPTION 'Ledger schema/owner/policy changed; inspect before granting';
  END IF;
  IF EXISTS (
    SELECT 1 FROM (VALUES
      ('status_exchanges_rows','SELECT'), ('status_exchanges_rows','INSERT'),
      ('status_exchanges_rows','UPDATE'), ('status_exchanges_oplog','SELECT'),
      ('status_exchanges_oplog','INSERT'), ('status_exchanges_cursor','SELECT'),
      ('status_exchanges_identity','SELECT')
    ) AS required(table_name, privilege)
    WHERE has_table_privilege('scitex_cards_dm','public.' || table_name,privilege)
  ) THEN
    RAISE EXCEPTION 'ACL prestate changed; do not replay this incident recipe';
  END IF;
END
$guard$;
GRANT SELECT ON public.status_exchanges_rows, public.status_exchanges_oplog,
  public.status_exchanges_cursor, public.status_exchanges_identity TO scitex_cards_dm;
GRANT INSERT, UPDATE ON public.status_exchanges_rows TO scitex_cards_dm;
GRANT INSERT ON public.status_exchanges_oplog TO scitex_cards_dm;
COMMIT;
