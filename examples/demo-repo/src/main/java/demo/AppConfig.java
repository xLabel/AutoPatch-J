package demo;

public class AppConfig {
    private final String mode;

    public AppConfig(String mode) {
        // BUG: missing-constructor-null-check
        this.mode = mode;
    }

    public String getMode() {
        return mode;
    }
}
