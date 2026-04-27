package demo;

import java.io.*;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Optional;

public class LegacyConfig {
    
    public void processData(String input) throws IOException, NoSuchAlgorithmException {
        // BUG: weak-crypto-md5
        MessageDigest md = MessageDigest.getInstance("MD5");
        
        // BUG: unclosed-io-stream
        FileInputStream fis = new FileInputStream("config.txt");
        int data = fis.read();
        
        Optional<String> opt = Optional.ofNullable(input);
        // BUG: optional-get-without-check
        System.out.println("Input: " + opt.get());
    }

    public boolean isDebug(AppConfig config) {
        // BUG: unsafe-equals-order
        return config.getMode().equals("debug");
    }
}
