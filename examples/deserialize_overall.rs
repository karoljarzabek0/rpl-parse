use std::fs::File;
use std::io::BufReader;
use std::time::Instant;
use rustyy::ProduktyLecznicze;

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

    println!("Deserializing XML...");
    let result: Result<ProduktyLecznicze, _> = quick_xml::de::from_reader(reader);
    let duration = start_time.elapsed();

    match result {
        Ok(data) => {
            println!("Success! Deserialization took: {:?}", duration);
            println!("stanNaDzien: {}", data.stan_na_dzien);
            println!("Total products: {}", data.produkty.len());

            if let Some(first) = data.produkty.first() {
                println!("\nFirst Product Sample:");
                println!("  ID: {}", first.id);
                println!("  Name: {}", first.nazwa_produktu);
                println!("  Active Substance (Powszechnie Stosowana): {}", first.nazwa_powszechnie_stosowana);
                println!("  Form: {}", first.nazwa_postaci_farmaceutycznej);
                if let Some(ref atc) = first.kody_atc {
                    println!("  ATC Codes: {:?}", atc.kody);
                }
                if let Some(ref active) = first.substancje_czynne {
                    println!("  Active Substances Details:");
                    for sub in &active.substancje {
                        println!(
                            "    - {}: {} {}",
                            sub.nazwa_substancji, sub.ilosc_substancji, sub.jednostka_miary_ilosci_substancji
                        );
                    }
                }
                if let Some(ref packaging) = first.opakowania {
                    println!("  Packaging count: {}", packaging.opakowania.len());
                }
            }
        }
        Err(e) => {
            eprintln!("Deserialization failed: {}", e);
        }
    }
}
