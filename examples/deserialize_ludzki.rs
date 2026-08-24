use std::fs::File;
use std::io::BufReader;
use std::time::Instant;
use rustyy::deserialize_ludzki;
use quick_xml::reader::Reader;
use quick_xml::events::Event;
use quick_xml::de::Deserializer;

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
    
    // Create a quick-xml Reader to find the root element
    let mut reader = Reader::from_reader(BufReader::new(file));
    let mut buf = Vec::new();

    // Advance the reader to skip the root <produktyLecznicze> tag
    loop {
        match reader.read_event_into(&mut buf) {
            Ok(Event::Start(ref e)) if e.name().as_ref() == b"produktyLecznicze" => {
                break;
            }
            Ok(Event::Eof) => {
                eprintln!("EOF reached before finding root tag");
                return;
            }
            _ => {}
        }
        buf.clear();
    }

    println!("Positioned after root tag. Creating Deserializer...");
    // Construct the Deserializer from the remaining reader stream
    let mut deserializer = Deserializer::from_reader(reader.into_inner());

    // Call the deserializer helper directly
    println!("Executing deserialize_ludzki directly on the file stream...");
    match deserialize_ludzki(&mut deserializer) {
        Ok(products) => {
            let duration = start_time.elapsed();
            println!("Success! Deserialization took: {:?}", duration);
            println!("Total 'ludzki' products deserialized: {}", products.len());

            if let Some(first) = products.first() {
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
