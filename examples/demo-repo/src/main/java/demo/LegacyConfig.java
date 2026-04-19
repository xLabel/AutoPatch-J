package demo;

public class LegacyConfig {
    public boolean isDebug(AppConfig config) {
        return config.getMode().equals("debug");
    }
}
