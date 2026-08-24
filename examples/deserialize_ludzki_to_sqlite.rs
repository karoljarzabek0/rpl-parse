use rpl_parse::{ProduktLeczniczy, ProduktyLecznicze};
use sqlx::sqlite::{SqliteConnectOptions, SqliteJournalMode, SqlitePoolOptions, SqliteSynchronous};
use sqlx::{Pool, Sqlite};
use std::fs::File;
use std::io::BufReader;
use std::str::FromStr;
use std::time::Instant;

const SCHEMA: &str = include_str!("schema.sql");

async fn initialize_schema(pool: &Pool<Sqlite>) -> Result<(), sqlx::Error> {
    sqlx::raw_sql(SCHEMA).execute(pool).await?;
    Ok(())
}

async fn insert_products(
    pool: &Pool<Sqlite>,
    products: &[ProduktLeczniczy],
    batch_size: usize,
) -> Result<InsertStats, Box<dyn std::error::Error>> {
    let mut stats = InsertStats::default();

    for chunk in products.chunks(batch_size) {
        let mut tx = pool.begin().await?;

        for product in chunk {
            sqlx::query(
                r#"
                INSERT INTO produkty_lecznicze (
                    id, nazwa_produktu, rodzaj_preparatu, nazwa_powszechnie_stosowana,
                    nazwa_poprzednia_produktu, moc, nazwa_postaci_farmaceutycznej,
                    podmiot_odpowiedzialny, typ_procedury, numer_pozwolenia,
                    waznosc_pozwolenia, podstawa_prawna, zakaz_stosowania_u_zwierzat,
                    ulotka, charakterystyka, etykieto_ulotka,
                    etykieto_ulotka_import_rownolegly, oznaczenie_opakowan_import_rownolegly,
                    ulotka_import_rownolegly
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                "#,
            )
            .bind(product.id as i64)
            .bind(&product.nazwa_produktu)
            .bind(&product.rodzaj_preparatu)
            .bind(&product.nazwa_powszechnie_stosowana)
            .bind(&product.nazwa_poprzednia_produktu)
            .bind(&product.moc)
            .bind(&product.nazwa_postaci_farmaceutycznej)
            .bind(&product.podmiot_odpowiedzialny)
            .bind(&product.typ_procedury)
            .bind(&product.numer_pozwolenia)
            .bind(&product.waznosc_pozwolenia)
            .bind(&product.podstawa_prawna)
            .bind(&product.zakaz_stosowania_u_zwierzat)
            .bind(&product.ulotka)
            .bind(&product.charakterystyka)
            .bind(&product.etykieto_ulotka)
            .bind(&product.etykieto_ulotka_import_rownolegly)
            .bind(&product.oznaczenie_opakowan_import_rownolegly)
            .bind(&product.ulotka_import_rownolegly)
            .execute(&mut *tx)
            .await?;
            stats.products += 1;

            // 1. ATC Codes
            if let Some(ref atc_codes) = product.kody_atc {
                for code in &atc_codes.kody {
                    sqlx::query(
                        "INSERT INTO kody_atc (produkt_id, kod_atc) VALUES (?, ?)",
                    )
                    .bind(product.id as i64)
                    .bind(code)
                    .execute(&mut *tx)
                    .await?;
                    stats.atc_codes += 1;
                }
            }

            // 2. Routes of administration
            if let Some(ref routes) = product.drogi_podania {
                for route in &routes.drogi {
                    sqlx::query(
                        "INSERT INTO drogi_podania (produkt_id, droga_podania_nazwa) VALUES (?, ?)",
                    )
                    .bind(product.id as i64)
                    .bind(&route.droga_podania_nazwa)
                    .execute(&mut *tx)
                    .await?;
                    stats.routes += 1;
                }
            }

            // 3. Active substances
            if let Some(ref substances) = product.substancje_czynne {
                for sub in &substances.substancje {
                    sqlx::query(
                        r#"
                        INSERT INTO substancje_czynne (
                            produkt_id, nazwa_substancji, ilosc_substancji,
                            jednostka_miary_ilosci_substancji, ilosc_preparatu,
                            jednostka_miary_ilosci_preparatu, inny_opis_ilosci
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        "#,
                    )
                    .bind(product.id as i64)
                    .bind(&sub.nazwa_substancji)
                    .bind(&sub.ilosc_substancji)
                    .bind(&sub.jednostka_miary_ilosci_substancji)
                    .bind(&sub.ilosc_preparatu)
                    .bind(&sub.jednostka_miary_ilosci_preparatu)
                    .bind(&sub.inny_opis_ilosci)
                    .execute(&mut *tx)
                    .await?;
                    stats.active_substances += 1;
                }
            }

            // 4. Packaging
            if let Some(ref packaging_list) = product.opakowania {
                for pkg in &packaging_list.opakowania {
                    sqlx::query(
                        r#"
                        INSERT INTO opakowania (
                            opakowanie_id, produkt_id, kod_gtin,
                            kategoria_dostepnosci, skasowane, numer_eu,
                            dystrybutor_rownolegly
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        "#,
                    )
                    .bind(pkg.id as i64)
                    .bind(product.id as i64)
                    .bind(&pkg.kod_gtin)
                    .bind(&pkg.kategoria_dostepnosci)
                    .bind(&pkg.skasowane)
                    .bind(&pkg.numer_eu)
                    .bind(&pkg.dystrybutor_rownolegly)
                    .execute(&mut *tx)
                    .await?;
                    stats.packaging += 1;

                    // Packaging units
                    if let Some(ref units) = pkg.jednostki_opakowania {
                        for unit in &units.jednostki {
                            sqlx::query(
                                r#"
                                INSERT INTO jednostki_opakowania (
                                    opakowanie_id, produkt_id, liczba_opakowan,
                                    rodzaj_opakowania, pojemnosc, jednostka_pojemnosci,
                                    informacje_dodatkowe
                                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                                "#,
                            )
                            .bind(pkg.id as i64)
                            .bind(product.id as i64)
                            .bind(&unit.liczba_opakowan)
                            .bind(&unit.rodzaj_opakowania)
                            .bind(&unit.pojemnosc)
                            .bind(&unit.jednostka_pojemnosci)
                            .bind(&unit.informacje_dodatkowe)
                            .execute(&mut *tx)
                            .await?;
                            stats.packaging_units += 1;
                        }
                    }

                    // President approvals (Zgody Prezesa) & Foreign GTINs
                    if let Some(ref approvals) = pkg.zgody_prezesa {
                        for approval in &approvals.zgody {
                            if let Some(ref foreign_gtins) = approval.gtin_zagraniczne {
                                for foreign in &foreign_gtins.gtin_zagraniczne {
                                    sqlx::query(
                                        r#"
                                        INSERT INTO zgody_prezesa (
                                            opakowanie_id, produkt_id, nr_zgody_prezesa, gtin_zagraniczny
                                        ) VALUES (?, ?, ?, ?)
                                        "#,
                                    )
                                    .bind(pkg.id as i64)
                                    .bind(product.id as i64)
                                    .bind(&approval.nr_zgody_prezesa)
                                    .bind(&foreign.numer)
                                    .execute(&mut *tx)
                                    .await?;
                                    stats.approvals += 1;
                                }
                            } else {
                                sqlx::query(
                                    r#"
                                    INSERT INTO zgody_prezesa (
                                        opakowanie_id, produkt_id, nr_zgody_prezesa, gtin_zagraniczny
                                    ) VALUES (?, ?, ?, NULL)
                                    "#,
                                )
                                .bind(pkg.id as i64)
                                .bind(product.id as i64)
                                .bind(&approval.nr_zgody_prezesa)
                                .execute(&mut *tx)
                                .await?;
                                stats.approvals += 1;
                            }
                        }
                    }
                }
            }

            // 5. Manufacturers
            if let Some(ref manufacturers) = product.dane_o_wytworcy {
                for mfg in &manufacturers.wytworcy {
                    sqlx::query(
                        r#"
                        INSERT INTO wytworcy (
                            produkt_id, nazwa_wytworcy_importera,
                            kraj_wytworcy_importera, kraj_eksportu,
                            podmiot_odpowiedzialny_w_kraju_eksportu
                        ) VALUES (?, ?, ?, ?, ?)
                        "#,
                    )
                    .bind(product.id as i64)
                    .bind(&mfg.nazwa_wytworcy_importera)
                    .bind(&mfg.kraj_wytworcy_importera)
                    .bind(&mfg.kraj_eksportu)
                    .bind(&mfg.podmiot_odpowiedzialny_w_kraju_eksportu)
                    .execute(&mut *tx)
                    .await?;
                    stats.manufacturers += 1;
                }
            }

            // 6. Educational materials
            if let Some(ref edu) = product.materialy_edukacyjne {
                if let Some(ref for_patient) = edu.dla_pacjenta {
                    for mat in &for_patient.materialy {
                        sqlx::query(
                            r#"
                            INSERT INTO materialy_edukacyjne (
                                produkt_id, typ_odbiorcy, material, nazwa_materialu
                            ) VALUES (?, 'dla_pacjenta', ?, ?)
                            "#,
                        )
                        .bind(product.id as i64)
                        .bind(&mat.material)
                        .bind(&mat.nazwa_materialu)
                        .execute(&mut *tx)
                        .await?;
                        stats.educational_materials += 1;
                    }
                }
                if let Some(ref for_medic) = edu.dla_medyka {
                    for mat in &for_medic.materialy {
                        sqlx::query(
                            r#"
                            INSERT INTO materialy_edukacyjne (
                                produkt_id, typ_odbiorcy, material, nazwa_materialu
                            ) VALUES (?, 'dla_medyka', ?, ?)
                            "#,
                        )
                        .bind(product.id as i64)
                        .bind(&mat.material)
                        .bind(&mat.nazwa_materialu)
                        .execute(&mut *tx)
                        .await?;
                        stats.educational_materials += 1;
                    }
                }
            }
        }

        tx.commit().await?;
        println!(
            "  -> Committed batch: {} / {} products inserted...",
            stats.products,
            products.len()
        );
    }

    Ok(stats)
}

#[derive(Default, Debug)]
struct InsertStats {
    products: usize,
    atc_codes: usize,
    routes: usize,
    active_substances: usize,
    packaging: usize,
    packaging_units: usize,
    approvals: usize,
    manufacturers: usize,
    educational_materials: usize,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let xml_path = "data/overall.xml";
    let db_path = "data/demo_rpl.db";

    println!("==================================================");
    println!("  RPL XML Deserializer & SQLite Inserter");
    println!("==================================================");
    println!("XML source: {}", xml_path);
    println!("Database:   {}", db_path);

    // 1. Clean up existing demo database file if present
    if std::path::Path::new(db_path).exists() {
        std::fs::remove_file(db_path)?;
        println!("Removed previous demo database file: {}", db_path);
    }

    // 2. Deserialize XML
    println!("\n[1/3] Deserializing XML data (filtering for 'ludzki')...");
    let start_de = Instant::now();
    let file = File::open(xml_path)?;
    let reader = BufReader::new(file);
    let dataset: ProduktyLecznicze = quick_xml::de::from_reader(reader)?;
    let de_duration = start_de.elapsed();
    println!(
        "✓ Deserialized {} products in {:?}",
        dataset.produkty.len(),
        de_duration
    );

    // 3. Connect to SQLite database with optimized PRAGMAs
    println!("\n[2/3] Connecting to SQLite & applying schema...");
    let connect_opts = SqliteConnectOptions::from_str(&format!("sqlite://{}", db_path))?
        .create_if_missing(true)
        .journal_mode(SqliteJournalMode::Wal)
        .synchronous(SqliteSynchronous::Normal);

    let pool = SqlitePoolOptions::new()
        .max_connections(5)
        .connect_with(connect_opts)
        .await?;

    initialize_schema(&pool).await?;
    println!("✓ Schema created successfully.");

    // 4. Insert data in batched transactions
    println!("\n[3/3] Inserting records into SQLite...");
    let start_insert = Instant::now();
    let batch_size = 2_500;
    let stats = insert_products(&pool, &dataset.produkty, batch_size).await?;
    let insert_duration = start_insert.elapsed();

    println!("\n==================================================");
    println!("  Insertion Summary");
    println!("==================================================");
    println!("Insertion Time:        {:?}", insert_duration);
    println!("Total Products:        {}", stats.products);
    println!("ATC Codes:             {}", stats.atc_codes);
    println!("Routes of Admin:       {}", stats.routes);
    println!("Active Substances:     {}", stats.active_substances);
    println!("Packaging:             {}", stats.packaging);
    println!("Packaging Units:       {}", stats.packaging_units);
    println!("President Approvals:   {}", stats.approvals);
    println!("Manufacturers:         {}", stats.manufacturers);
    println!("Educational Materials: {}", stats.educational_materials);

    // 5. Verification query
    println!("\n==================================================");
    println!("  Database Query Verification");
    println!("==================================================");
    let sample_product: (i64, String, String, String, String) = sqlx::query_as(
        r#"
        SELECT id, nazwa_produktu, nazwa_powszechnie_stosowana, moc, podmiot_odpowiedzialny
        FROM produkty_lecznicze
        LIMIT 1
        "#,
    )
    .fetch_one(&pool)
    .await?;

    println!("First Product in DB:");
    println!("  ID:                   {}", sample_product.0);
    println!("  Name:                 {}", sample_product.1);
    println!("  Active Substance:     {}", sample_product.2);
    println!("  Strength:             {}", sample_product.3);
    println!("  Responsible Entity:   {}", sample_product.4);

    let active_subs: Vec<(String, String, String)> = sqlx::query_as(
        r#"
        SELECT nazwa_substancji, ilosc_substancji, jednostka_miary_ilosci_substancji
        FROM substancje_czynne
        WHERE produkt_id = ?
        "#,
    )
    .bind(sample_product.0)
    .fetch_all(&pool)
    .await?;

    println!("  Substances:");
    for (name, qty, unit) in active_subs {
        println!("    - {} ({} {})", name, qty, unit);
    }

    let pkg_count: (i64,) = sqlx::query_as(
        "SELECT COUNT(*) FROM opakowania WHERE produkt_id = ?",
    )
    .bind(sample_product.0)
    .fetch_one(&pool)
    .await?;
    println!("  Associated Packaging count: {}", pkg_count.0);

    let total_db_products: (i64,) = sqlx::query_as("SELECT COUNT(*) FROM produkty_lecznicze")
        .fetch_one(&pool)
        .await?;
    println!("\nTotal products count in SQLite: {}", total_db_products.0);

    pool.close().await;
    println!("\n✓ Database demo completed successfully. File saved at: {}", db_path);

    Ok(())
}
