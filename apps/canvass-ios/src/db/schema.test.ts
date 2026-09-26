import { CACHE_TABLES, FORBIDDEN_COLUMN_PATTERNS, MIGRATIONS, SCHEMA_VERSION } from './schema';

const DDL = MIGRATIONS.join('\n');

/** Every `CREATE TABLE <name> ( ... )` body in the migrations, keyed by table. */
function tableBodies(): Map<string, string> {
  const bodies = new Map<string, string>();
  const re = /CREATE TABLE (\w+)\s*\(([\s\S]*?)\n\s*\)/g;

  for (let m = re.exec(DDL); m !== null; m = re.exec(DDL)) {
    bodies.set(m[1], m[2]);
  }

  return bodies;
}

/** Column names declared in a table body, ignoring constraint lines and comments. */
function columnNames(body: string): string[] {
  return body
    .split('\n')
    .map((line) => line.replace(/--.*$/, '').trim())
    .filter((line) => line.length > 0)
    .filter((line) => !/^(PRIMARY|FOREIGN|UNIQUE|CHECK|CONSTRAINT)\b/i.test(line))
    .map((line) => line.split(/\s+/)[0])
    .filter((name) => /^\w+$/.test(name));
}

describe('schema', () => {
  it('declares every table the cache is wiped from', () => {
    const declared = new Set(tableBodies().keys());

    // CACHE_TABLES drives P1-05's wipe. A table in the schema but missing
    // from that list would survive End Shift holding voter names.
    for (const table of CACHE_TABLES) {
      expect(declared.has(table)).toBe(true);
    }
    expect(declared.size).toBe(CACHE_TABLES.length);
  });

  it('never stores a turnout or lean score on the device', () => {
    // CLAUDE.md, spec §11: scores stay server-side. This asserts it against
    // the DDL so a later task cannot add one to a cache table unnoticed.
    const offenders: string[] = [];

    for (const [table, body] of tableBodies()) {
      for (const column of columnNames(body)) {
        for (const pattern of FORBIDDEN_COLUMN_PATTERNS) {
          if (pattern.test(column)) offenders.push(`${table}.${column}`);
        }
      }
    }

    expect(offenders).toEqual([]);
  });

  it('allows utility_version, which is a config pointer and not a score', () => {
    // Guards the negative lookahead in the /utility(?!_version)/ pattern: if
    // that regression fires, the test above starts failing for the wrong
    // reason and someone "fixes" it by dropping the utility guard entirely.
    const utilityColumns = columnNames(tableBodies().get('cache_meta') ?? '');
    expect(utilityColumns).toContain('utility_version');
  });

  it('makes client_event_id unique, which is what makes sync idempotent', () => {
    // §9.3 step 3. Without the constraint a retried batch duplicates
    // outcomes locally, and the local count stops matching the server's.
    expect(tableBodies().get('outbox')).toMatch(/client_event_id\s+TEXT\s+PRIMARY KEY/);
  });

  it('keeps SCHEMA_VERSION in step with the migration list', () => {
    expect(SCHEMA_VERSION).toBe(MIGRATIONS.length);
    expect(SCHEMA_VERSION).toBeGreaterThan(0);
  });

  it('orders CACHE_TABLES children before parents', () => {
    // Deleting candidate_stops before the tables referencing it would fail
    // with foreign keys on.
    const order = CACHE_TABLES.indexOf.bind(CACHE_TABLES);

    expect(order('walk_matrix')).toBeLessThan(order('candidate_stops'));
    expect(order('stop_people')).toBeLessThan(order('candidate_stops'));
  });
});
