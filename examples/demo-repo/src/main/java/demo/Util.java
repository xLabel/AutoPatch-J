package demo;

import java.util.Random;

public class Util {
    public int generateId() {
        // BUG: insecure-random
        Random rand = new Random();
        return rand.nextInt();
    }
}
