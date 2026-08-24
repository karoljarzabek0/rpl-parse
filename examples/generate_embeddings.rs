use aws_config::BehaviorVersion;
use aws_sdk_s3::config::Region;
use aws_sdk_s3::Client as S3Client;
use byteorder::{LittleEndian, WriteBytesExt};
use futures::stream::{self, StreamExt};
use indicatif::{ProgressBar, ProgressStyle};
use serde::{Deserialize, Serialize};
use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};
use sqlx::{Pool, Sqlite};
use std::path::Path;
use std::str::FromStr;
use std::sync::Arc;
use std::time::Instant;
use tokenizers::Tokenizer;

const S3_BUCKET: &str = "plek";
const S3_PREFIX: &str = "md/";
const S3_ENDPOINT: &str = "https://s3.waw.io.cloud.ovh.net";
const S3_REGION: &str = "waw";
const MODEL_NAME: &str = "OPI-PIB/PolDense-400M";
const CHUNK_SIZE: usize = 4096;
const CHUNK_OVERLAP: usize = 256;
const STRIDE: usize = CHUNK_SIZE - CHUNK_OVERLAP; // 3840
const EMBEDDING_SERVICE_URL: &str = "http://127.0.0.1:8000";

#[derive(Debug, Serialize)]
struct EmbedChunksRequest {
    id: Option<i64>,
    chunks: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct EmbedChunksResponse {
    id: Option<i64>,
    chunk_count: usize,
    dimension: usize,
    embedding: Vec<f32>,
}

#[derive(Debug)]
struct ProcessedDoc {
    produkt_id: i64,
    filename: String,
    chunk_count: usize,
    total_tokens: usize,
    char_length: usize,
    embedding: Vec<f32>,
}

/// Splits raw markdown text into 4096-token chunks with 256-token overlap using the HF tokenizer.
fn chunk_text(text: &str, tokenizer: &Tokenizer) -> Result<(Vec<String>, usize), Box<dyn std::error::Error + Send + Sync>> {
    let encoding = tokenizer.encode(text, false)?;
    let token_ids = encoding.get_ids();
    let total_tokens = token_ids.len();

    if total_tokens == 0 {
        return Ok((Vec::new(), 0));
    }

    let mut chunks = Vec::new();
    if total_tokens <= CHUNK_SIZE {
        let chunk_str = tokenizer.decode(token_ids, false)?;
        chunks.push(chunk_str);
    } else {
        let mut i = 0;
        while i < total_tokens {
            let end = (i + CHUNK_SIZE).min(total_tokens);
            let slice = &token_ids[i..end];
            let chunk_str = tokenizer.decode(slice, false)?;
            chunks.push(chunk_str);
            if end >= total_tokens {
                break;
            }
            i += STRIDE;
        }
    }

    Ok((chunks, total_tokens))
}

/// Computes the document embedding by sending chunks to the embedding service (or GPU worker)
/// which generates chunk embeddings and takes their mean vector.
async fn request_document_embedding(
    http_client: &reqwest::Client,
    produkt_id: i64,
    chunks: Vec<String>,
) -> Result<Vec<f32>, Box<dyn std::error::Error + Send + Sync>> {
    let req = EmbedChunksRequest {
        id: Some(produkt_id),
        chunks,
    };

    let url = format!("{}/embed_chunks", EMBEDDING_SERVICE_URL);
    let resp = http_client
        .post(&url)
        .json(&req)
        .send()
        .await?
        .error_for_status()?;

    let data: EmbedChunksResponse = resp.json().await?;
    Ok(data.embedding)
}

/// Initializes SQLite schema for storing 1024-dimensional document embeddings.
async fn init_db(pool: &Pool<Sqlite>) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        CREATE TABLE IF NOT EXISTS dokumenty_embeddings (
            produkt_id INTEGER PRIMARY KEY,
            filename TEXT NOT NULL,
            chunk_count INTEGER NOT NULL,
            total_tokens INTEGER NOT NULL,
            doc_char_length INTEGER NOT NULL,
            embedding BLOB NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_emb_produkt_id ON dokumenty_embeddings(produkt_id);
        "#,
    )
    .execute(pool)
    .await?;

    Ok(())
}

/// Converts a Vec<f32> embedding into a little-endian binary blob.
fn embedding_to_blob(embedding: &[f32]) -> Vec<u8> {
    let mut bytes = Vec::with_capacity(embedding.len() * 4);
    for &val in embedding {
        bytes.write_f32::<LittleEndian>(val).unwrap();
    }
    bytes
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    println!("══════════════════════════════════════════════════════════════");
    println!("  PolDense-400M S3 Fetch & Document Embedding Pipeline (Rust) ");
    println!("══════════════════════════════════════════════════════════════\n");

    let args: Vec<String> = std::env::args().collect();
    let limit: usize = if args.len() > 1 {
        args[1].parse().unwrap_or(100)
    } else {
        100
    };

    println!("• Configuration:");
    println!("  - S3 Bucket:        {}", S3_BUCKET);
    println!("  - S3 Endpoint:      {}", S3_ENDPOINT);
    println!("  - Model:            {}", MODEL_NAME);
    println!("  - Context Window:   {} tokens (Overlap: {} tokens)", CHUNK_SIZE, CHUNK_OVERLAP);
    println!("  - Aggregation:      Mean of chunk embeddings -> 1024-d Document Vector");
    println!("  - Target Limit:     {} documents\n", limit);

    // 1. Initialize AWS S3 client
    println!("Connecting to S3...");
    let sdk_config = aws_config::load_defaults(BehaviorVersion::latest()).await;
    let s3_config = aws_sdk_s3::config::Builder::from(&sdk_config)
        .region(Region::new(S3_REGION))
        .endpoint_url(S3_ENDPOINT)
        .force_path_style(true)
        .build();
    let s3_client = Arc::new(S3Client::from_conf(s3_config));

    // 2. Initialize Hugging Face Tokenizer
    println!("Loading tokenizer for {}...", MODEL_NAME);
    let tokenizer = Arc::new(Tokenizer::from_pretrained(MODEL_NAME, None)?);
    println!("Tokenizer loaded (Vocab size: {})", tokenizer.get_vocab_size(true));

    // 3. Initialize SQLite Database
    let db_path = "data/embeddings.db";
    let db_options = SqliteConnectOptions::from_str(&format!("sqlite://{}", db_path))?
        .create_if_missing(true);
    let pool = SqlitePoolOptions::new()
        .max_connections(5)
        .connect_with(db_options)
        .await?;
    init_db(&pool).await?;
    println!("Database initialized at {}\n", db_path);

    // 4. List keys from S3
    println!("Listing first {} documents from s3://{}/{}...", limit, S3_BUCKET, S3_PREFIX);
    let mut list_resp = s3_client
        .list_objects_v2()
        .bucket(S3_BUCKET)
        .prefix(S3_PREFIX)
        .max_keys(limit as i32)
        .send()
        .await?;

    let mut keys: Vec<String> = Vec::new();
    if let Some(contents) = list_resp.contents.take() {
        for obj in contents {
            if let Some(key) = obj.key {
                if key.ends_with(".md") && key != S3_PREFIX {
                    keys.push(key);
                }
            }
        }
    }
    println!("Found {} documents to process.\n", keys.len());

    let http_client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(60))
        .build()?;

    // Check if Python embedding service is running; if not, notify user
    let health_check = http_client.get(format!("{}/health", EMBEDDING_SERVICE_URL)).send().await;
    if health_check.is_err() {
        eprintln!("\n⚠️  Note: Local embedding service at {} is not running.", EMBEDDING_SERVICE_URL);
        eprintln!("   You can start it with: cd python && uv run python embed_service.py\n");
        eprintln!("   Alternatively, you can run the standalone python embedding script:");
        eprintln!("   cd python && uv run python generate_sample_embeddings.py --limit {}\n", limit);
        return Err("Embedding service unreachable. Please start embed_service.py or run python script.".into());
    }

    // 5. Process documents concurrently
    let pb = ProgressBar::new(keys.len() as u64);
    pb.set_style(
        ProgressStyle::default_bar()
            .template("[{elapsed_precise}] [{bar:40.cyan/blue}] {pos}/{len} docs ({per_sec}) {msg}")?
            .progress_chars("━╸─"),
    );

    let start_time = Instant::now();
    let concurrency = 8;

    let results = stream::iter(keys)
        .map(|key| {
            let s3 = Arc::clone(&s3_client);
            let tok = Arc::clone(&tokenizer);
            let client = http_client.clone();
            let pb = pb.clone();

            tokio::spawn(async move {
                let filename = Path::new(&key)
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_string();
                let prod_id_str = filename.replace(".md", "");
                let prod_id: i64 = prod_id_str.parse().unwrap_or(0);

                // Fetch document from S3
                let obj = s3.get_object().bucket(S3_BUCKET).key(&key).send().await?;
                let bytes = obj.body.collect().await?.into_bytes();
                let text = String::from_utf8_lossy(&bytes).to_string();
                let char_len = text.len();

                // Tokenize & split into 4096-token chunks with 256 overlap
                let (chunks, total_tokens) = chunk_text(&text, &tok)?;
                let chunk_count = chunks.len();

                // Compute aggregated document embedding via mean of chunk vectors
                let embedding = request_document_embedding(&client, prod_id, chunks).await?;

                pb.inc(1);
                pb.set_message(format!("ID: {}", prod_id));

                Ok::<ProcessedDoc, Box<dyn std::error::Error + Send + Sync>>(ProcessedDoc {
                    produkt_id: prod_id,
                    filename,
                    chunk_count,
                    total_tokens,
                    char_length: char_len,
                    embedding,
                })
            })
        })
        .buffer_unordered(concurrency)
        .collect::<Vec<_>>()
        .await;

    pb.finish_with_message("Done!");

    // 6. Save to SQLite database
    let mut total_chunks = 0;
    let mut total_tokens = 0;
    let mut success_count = 0;

    let mut tx = pool.begin().await?;
    for res in results {
        match res {
            Ok(Ok(doc)) => {
                total_chunks += doc.chunk_count;
                total_tokens += doc.total_tokens;
                success_count += 1;

                let blob = embedding_to_blob(&doc.embedding);
                sqlx::query(
                    r#"
                    INSERT OR REPLACE INTO dokumenty_embeddings (
                        produkt_id, filename, chunk_count, total_tokens, doc_char_length, embedding
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    "#,
                )
                .bind(doc.produkt_id)
                .bind(&doc.filename)
                .bind(doc.chunk_count as i64)
                .bind(doc.total_tokens as i64)
                .bind(doc.char_length as i64)
                .bind(blob)
                .execute(&mut *tx)
                .await?;
            }
            Ok(Err(e)) => eprintln!("Error processing document: {}", e),
            Err(e) => eprintln!("Task join error: {}", e),
        }
    }
    tx.commit().await?;

    let elapsed = start_time.elapsed().as_secs_f64();

    println!("\n╔═════════════════════════════════════════════════════════════════════╗");
    println!("║                    Pipeline Execution Summary                       ║");
    println!("╠═════════════════════════════════════════════════════════════════════╣");
    println!("║  • Documents Processed:      {:>6}                                 ║", success_count);
    println!("║  • Total Chunks Encoded:     {:>6}                                 ║", total_chunks);
    println!("║  • Total Tokens Processed:   {:>10}                            ║", total_tokens);
    println!("║  • Avg Chunks / Document:    {:>6.2}                                 ║", total_chunks as f64 / success_count.max(1) as f64);
    println!("║  • Total Duration:           {:>6.2}s ({:.1} docs/sec)             ║", elapsed, success_count as f64 / elapsed);
    println!("║  • Target SQLite DB:         data/embeddings.db                    ║");
    println!("╚═════════════════════════════════════════════════════════════════════╝\n");

    Ok(())
}
