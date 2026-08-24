use std::fs::File;
use std::io::BufReader;
use std::time::Instant;

fn main() {
    let file_path = "data/overall.xml";
    println!("Opening file: {}", file_path);

    let start_time = Instant::now();
    let file = match File::open(file_path) {
        Ok(file) => file,
        Err(e) => {
            eprintln!("Failed to open file: {}", e);
            return;
        }
    };

    let reader = BufReader::new(file);

    println!("Executing deserialize_ludzki directly on the file stream...");
    match quick_xml::de::from_reader::<_, rpl_parse::ProduktyLecznicze>(reader) {
        Ok(data) => {
            let duration = start_time.elapsed();
            println!("Success! Deserialization took: {:?}", duration);
            println!("Total 'ludzki' products deserialized: {}", data.produkty.len());

            if let Some(first) = data.produkty.first() {
                println!("\nFirst Product Sample:");
                println!("  ID: {}", first.id);
                println!("  Name: {}", first.nazwa_produktu);
                println!("  Type (rodzajPreparatu): {}", first.rodzaj_preparatu);
            }
        }
        Err(e) => {
            eprintln!("Deserialization failed: {}", e);
        }
    }
}
